"""Native H3 video and Bolt qualification tests: synthetic files, no model loads or hardware probes.

With gating on, a native recipe runs only after its own check passed. H3 video and Bolt had no check, so the gate
could only refuse them. These cover the two checks and the gate reading them."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_checks
import windows_cuda
import windows_engine_checks as engines
import windows_features as features
import windows_runtime

PLAIN = {"prompt": "a baker", "preset": "small", "quality": "fh8", "frames": 124}


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "folder", lambda: tmp_path / "evidence")
    monkeypatch.setattr(engines, "prerequisites", lambda kind: "")
    value = {"contract": 1, "kind": "video", "recipe": engines.recipe("video"), "hardware": {}, "python_abi": "3.13",
             "models": {}, "engine": {"tf_h3.dll": "digest"}, "model_root": "example", "sources": {"one": "old"}, "guard": {}}
    def fingerprint(kind):
        return {**copy.deepcopy(value), "kind": kind, "recipe": engines.recipe(kind)}
    monkeypatch.setattr(engines, "fingerprint", fingerprint)
    return value


def passed(kind):
    features.save_pass(kind, {"ok": True, "fingerprint": engines.fingerprint(kind), "model_sha256": {}, "results": []})


@pytest.mark.parametrize("kind", engines.KINDS)
def test_a_pass_is_success_only_and_a_changed_engine_asks_for_the_check_again(evidence, tmp_path, monkeypatch, kind):
    observed = []
    def run(command, output, validate, result, timeout):
        observed.append(command)
        result.update(rendered=True, elapsed_s=1)
        return result
    monkeypatch.setattr(engines.native, "observe_native", run)
    assert not engines.status(kind)["qualified"] and "Feature checks" in engines.status(kind)["why"]
    assert engines.qualify(kind, tmp_path / kind, "check-job")["ok"]
    assert engines.status(kind)["qualified"]
    command = observed[0]
    assert command[command.index("--feature") + 1] == kind and "--worker" in command
    assert command[3].endswith("windows-native-engine-check.py")
    evidence["sources"]["one"] = "new"  # the app's code moved on: the pass stands (balanced), and says so
    assert engines.status(kind)["qualified"] and engines.status(kind)["runtime_updated"]
    evidence["engine"]["tf_h3.dll"] = "another build"  # the engine file itself changed: checked again
    found = engines.status(kind)
    assert not found["qualified"] and "changed" in found["why"]


def test_a_failed_check_keeps_the_earlier_pass_and_its_observations(evidence, tmp_path, monkeypatch):
    passed("video")
    previous = features.saved("video", "")
    def failed(command, output, validate, result, timeout):
        result["measurements"] = {"samples": 10}
        raise RuntimeError("native memory floor reached")
    monkeypatch.setattr(engines.native, "observe_native", failed)
    with pytest.raises(RuntimeError, match="native memory floor"):
        engines.qualify("video", tmp_path / "check", "job1")
    assert features.saved("video", "") == previous and engines.status("video")["qualified"]
    assert features.saved("video", "-attempt")["observations"]["measurements"]["samples"] == 10


@pytest.mark.parametrize("kind", engines.KINDS)
def test_the_check_is_a_registered_supervised_queue_job(evidence, tmp_path, kind):
    assert windows_checks.parameters({"feature": kind}) == {"feature": kind}
    command, env, outputs = windows_checks.build(tmp_path, {"feature": kind}, "job")
    assert command[command.index("--feature") + 1] == kind and command[3].endswith("windows-native-engine-check.py")
    job = {"kind": "qualification", "params": {"feature": kind}}
    assert windows_checks.native_image_check(job) and windows_checks.gpu_check(job)
    assert windows_checks.timeout_seconds(job) > engines.TIMEOUT_S
    assert not env and len(outputs) == 1
    assert windows_checks.CHECKS[kind].title == engines.TITLES[kind]


def test_with_gating_on_video_is_refused_until_its_check_passes_and_then_held_to_what_it_ran(evidence, monkeypatch):
    monkeypatch.setattr(windows_cuda, "h3_why_not", lambda: "")
    monkeypatch.setattr(windows_runtime, "video_missing", lambda params=None: [])
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    with pytest.raises(windows_runtime.RouteUnavailable, match="Feature checks"):
        windows_runtime.require_native("video", PLAIN)
    passed("video")
    windows_runtime.require_native("video", PLAIN)
    windows_runtime.require_native("video", {**PLAIN, "frames": 107})  # shorter is inside it
    windows_runtime.require_native("video", {**PLAIN, "quality": "fh4"})  # fewer passes of the same recipe
    for outside in ({"preset": "native"}, {"quality": "fh20"}, {"frames": 192}, {"image": "renders/a.png"},
                    {"references": ["a.png"]}, {"loras": ["x"]}, {"last_frame": "a.png"}, {"steps": 20}):
        with pytest.raises(ValueError, match="Verified video scope"):
            windows_runtime.require_native("video", {**PLAIN, **outside})
    # the two video kinds without a check stay refused under the gate
    for kind in ("studio", "long"):
        with pytest.raises(windows_runtime.RouteUnavailable, match="has not been verified"):
            windows_runtime.require_native(kind, PLAIN)


def test_with_gating_off_video_runs_as_before_with_or_without_a_check(evidence, monkeypatch):
    monkeypatch.setattr(windows_cuda, "h3_why_not", lambda: "")
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    monkeypatch.setattr(windows_cuda, "why_not", lambda: "")
    monkeypatch.setattr(windows_runtime, "video_missing", lambda params=None: [])
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": False})
    windows_runtime.require_native("video", {**PLAIN, "preset": "native", "frames": 200})


@pytest.mark.parametrize("kind,name", [("video", "video"), ("bolt", "Bolt")])
def test_the_gate_reads_the_check_for_the_page(evidence, monkeypatch, kind, name):
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    raw = {"available": True, "installed": True, "why": "", "state": "available_unverified"}
    on, off = {"enabled": True}, {"enabled": False}
    found = windows_runtime.gate_native_state(name, raw, on)
    assert found["available"] is False and found["state"] == "needs_verification" and found["check_available"] is True
    assert "Feature checks" in found["why"] and "no Windows verification check yet" not in found["why"]
    assert windows_runtime.gate_native_state(name, raw, off)["available"] is True  # gating off: as before
    passed(kind)
    found = windows_runtime.gate_native_state(name, raw, on)
    assert found["available"] is True and found["qualified"] is True and found["state"] == "qualified"
    # a recipe that is not installed stays not installed, whatever was checked
    gone = windows_runtime.gate_native_state(name, {"available": False, "installed": False, "why": "no engine", "state": "not_installed"}, on)
    assert gone["available"] is False and gone["why"] == "no engine"


def test_other_native_recipes_without_a_check_are_still_refused_under_the_gate(evidence, monkeypatch):
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    found = windows_runtime.gate_native_state("studio", {"available": True, "installed": True, "why": ""}, {"enabled": True})
    assert found["available"] is False and "no Windows verification check yet" in found["why"]


def test_bolt_answers_under_the_gate_only_after_its_check(evidence, monkeypatch):
    import bolt

    class One:
        def present(self):
            return ""
        def request(self, stopped=None, wait=True, say=None):
            return "served"
    monkeypatch.setattr(bolt, "WINCUDA", True)
    monkeypatch.setattr(bolt, "engine", lambda: One())
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    with pytest.raises(RuntimeError, match="Run the NVIDIA Nemotron 3.5 Bolt check"):
        bolt.request()
    passed("bolt")
    assert bolt.request() == "served"
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": False})
    features.save_pass("bolt", {"ok": True, "fingerprint": {**engines.fingerprint("bolt"), "engine": {"old": "x"}}, "model_sha256": {}})
    assert bolt.request() == "served"  # gating off: no check is asked for


def test_the_video_check_renders_the_plain_clip_the_app_makes_and_validates_it(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_cuda, "translate", lambda argv: ["python", "windows_scripts.py", "video", *argv[2:]])
    command, env = engines.video_command(tmp_path / "check.mp4")
    assert command[:3] == ["python", "windows_scripts.py", "video"] and command[3] == engines.VIDEO_PROMPT and command[4].endswith("check.mp4")
    assert env == {"SEED": "7", "FRAMES": "124", "WIDTH": "864", "HEIGHT": "480", "ENGINE": "fasth3", "STEPS": "8"}
    import graphics
    clip = tmp_path / "check.mp4"
    with pytest.raises(RuntimeError, match="did not write"):
        engines.check_video(clip)
    clip.write_bytes(b"x")
    good = {"width": 864, "height": 480, "fps": 24.0, "seconds": 5.17, "audio": True}
    for found, why in ((good, None), ({**good, "audio": False}, "no sound"), ({**good, "width": 1280}, "canvas"),
                       ({**good, "seconds": 2.0}, "canvas"), ({**good, "fps": 30.0}, "canvas")):
        monkeypatch.setattr(graphics, "probe", lambda path, found=found: found)
        if why is None:
            engines.check_video(clip)
        else:
            with pytest.raises(RuntimeError, match=why):
                engines.check_video(clip)


def test_the_bolt_check_loads_asks_once_and_drains_the_engine(tmp_path, monkeypatch):
    import contextlib
    import urllib.request
    events = []

    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, *a):
            return json.dumps({"choices": [{"message": {"content": "It marks the start of a take."}}], "usage": {"completion_tokens": 8}}).encode()

    class One:
        process = None
        def check(self):
            return ""
        @contextlib.contextmanager
        def request(self):
            events.append("loaded")
            yield "http://127.0.0.1:1/v1"
        def release(self, why, wait_s=0):
            events.append("released")
            return True
    def opened(call, timeout=0):
        body = json.loads(call.data)
        events.append((call.full_url, body["max_tokens"], body["messages"][0]["content"]))
        return Reply()
    monkeypatch.setattr(engines, "bolt_engine", lambda: One())
    monkeypatch.setattr(urllib.request, "urlopen", opened)
    engines.worker("bolt", tmp_path)
    assert events == ["loaded", ("http://127.0.0.1:1/v1/chat/completions", 48, engines.BOLT_QUESTION), "released"]
    engines.check_bolt(tmp_path)
    (tmp_path / "bolt" / "answer.json").write_text(json.dumps({"answer": "ok"}))
    with pytest.raises(RuntimeError, match="did not record an answer"):
        engines.check_bolt(tmp_path)

    class Stuck(One):
        process = type("P", (), {"poll": lambda self: None})()
    monkeypatch.setattr(engines, "bolt_engine", lambda: Stuck())
    with pytest.raises(RuntimeError, match="did not drain"):
        engines.worker("bolt", tmp_path / "again")


def test_a_passed_level_opens_every_clip_no_larger_and_no_longer_and_nothing_beyond(evidence, monkeypatch):
    """The larger sizes and longer clips have checks of their own. A level covers what is smaller and shorter than it."""
    monkeypatch.setattr(windows_cuda, "h3_why_not", lambda: "")
    monkeypatch.setattr(windows_runtime, "video_missing", lambda params=None: [])
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    assert list(engines.VIDEO_LEVELS) == ["video", "video-long", "video-large", "video-sharp", "video-largest"]
    assert engines.VIDEO_LEVELS["video-largest"][1:] == (1376, 768, 362)  # the app's largest canvas and longest clip
    def runs(**settings):
        try:
            windows_runtime.require_native("video", {**PLAIN, **settings})
            return True
        except ValueError:
            return False
    passed("video")
    assert runs() and not runs(seconds=15, frames=None) and not runs(preset="sharp") and not runs(preset="p720")
    passed("video-long")  # small, 15 s
    assert runs(frames=362) and runs(frames=243) and not runs(preset="native") and engines.passed_levels() == ["video", "video-long"]
    passed("video-large")  # full size, 5 s: 720p and Native are inside it, 10 s at full size is not
    assert runs(preset="sharp") and runs(preset="native") and runs(preset="p720") and runs(preset="custom", width=1024, height=576)
    assert not runs(preset="sharp", frames=243) and not runs(preset="native", frames=192)
    assert not runs(preset="custom", width=1920, height=1088) and not runs(preset="custom")
    passed("video-sharp")  # full size, 10 s
    assert runs(preset="sharp", frames=243) and runs(preset="native", frames=192) and not runs(preset="sharp", frames=362)
    passed("video-largest")
    assert runs(preset="sharp", frames=362) and runs(preset="sharp", frames=362, quality="fh4")
    assert not runs(preset="sharp", quality="fh20") and not runs(preset="sharp", image="a.png")  # still not what a check ran
    found = engines.video_status()
    assert found["qualified"] and found["passed_levels"] == list(engines.VIDEO_LEVELS) and len(found["verified_recipe"]) == 5


def test_the_largest_level_alone_is_enough_and_a_larger_pass_does_not_need_the_first(evidence, monkeypatch):
    monkeypatch.setattr(windows_cuda, "h3_why_not", lambda: "")
    monkeypatch.setattr(windows_cuda, "configuration_error", lambda: "")
    monkeypatch.setattr(windows_runtime, "video_missing", lambda params=None: [])
    monkeypatch.setattr(windows_runtime, "mode", lambda: {"enabled": True})
    assert not engines.video_status()["qualified"]
    passed("video-largest")
    windows_runtime.require_native("video", PLAIN)
    windows_runtime.require_native("video", {**PLAIN, "preset": "sharp", "frames": 362})
    raw = {"available": True, "installed": True, "why": "", "state": "available_unverified"}
    assert windows_runtime.gate_native_state("video", raw, {"enabled": True})["available"] is True


@pytest.mark.parametrize("kind", [k for k in engines.VIDEO_LEVELS if k != "video"])
def test_each_level_renders_its_own_canvas_and_length_and_is_validated_against_them(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(windows_cuda, "translate", lambda argv: ["python", "windows_scripts.py", "video", *argv[2:]])
    _, width, height, frames = engines.VIDEO_LEVELS[kind]
    _, env = engines.video_command(tmp_path / "check.mp4", kind)
    assert (env["WIDTH"], env["HEIGHT"], env["FRAMES"], env["STEPS"]) == (str(width), str(height), str(frames), "8")
    assert f"{width}x{height}" in engines.scope_label(kind) and windows_checks.CHECKS[kind].title.startswith("H3 / FastH3 video: ")
    import graphics
    clip = tmp_path / "check.mp4"
    clip.write_bytes(b"x")
    good = {"width": width, "height": height, "fps": 24.0, "seconds": frames / 24, "audio": True}
    monkeypatch.setattr(graphics, "probe", lambda path: good)
    engines.check_video(clip, kind)
    monkeypatch.setattr(graphics, "probe", lambda path: {**good, "width": 512, "height": 288, "seconds": 2.0})  # another clip is not this level's
    with pytest.raises(RuntimeError, match="canvas"):
        engines.check_video(clip, kind)
