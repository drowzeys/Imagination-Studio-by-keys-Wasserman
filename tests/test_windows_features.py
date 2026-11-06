"""Independent Windows capabilities: local qualification, bounded settings and guarded API admission."""
import json
import importlib.util
import hashlib
import threading

import pytest

from test_windows_ui import client, server  # noqa: F401
import windows_features as features
import finish


@pytest.fixture
def qualification(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_WINDOWS_FEATURE_DIR", str(tmp_path))
    monkeypatch.setattr(features, "prerequisites", lambda kind: "")
    monkeypatch.setattr(features, "fingerprint", lambda kind: {"kind": kind, "version": 1})
    return tmp_path


def test_installation_alone_does_not_qualify_a_feature(qualification):
    found = features.status("hlg")
    assert found["installed"] and not found["qualified"] and not found["available"]
    assert found["state"] == "needs_qualification"
    assert "Machine > Feature checks" in found["why"] and "powershell" not in found["why"]


def test_qualification_is_local_recipe_specific_and_invalidates(qualification, monkeypatch):
    marker = qualification / "hlg.json"
    marker.write_text(json.dumps({"ok": True, "fingerprint": features.fingerprint("hlg")}))
    assert features.status("hlg")["available"]
    assert not features.status("finish")["available"]
    monkeypatch.setattr(features, "fingerprint", lambda kind: {"kind": kind, "version": 2})
    assert not features.status("hlg")["available"]
    assert "changed" in features.status("hlg")["why"]
    marker.write_text("invalid")
    assert features.status("hlg")["state"] == "qualification_error"


@pytest.mark.parametrize("changes", [
    {"seconds": 10.01}, {"width": 1920}, {"width": 960, "height": 960}, {"fps": 30}, {"height": 0},
    {"seconds": float("nan")}, {"fps": True},
])
def test_unqualified_media_settings_are_refused(changes):
    info = {"width": 1280, "height": 720, "seconds": 10, "fps": 24}
    features.check_media(info)
    with pytest.raises(ValueError):
        features.check_media({**info, **changes})


def test_temporary_headroom_refusal_does_not_remove_qualification(qualification, monkeypatch):
    (qualification / "hlg.json").write_text(json.dumps({"ok": True, "fingerprint": features.fingerprint("hlg")}))
    class Signals:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            return 100, 100000, 0, False
    monkeypatch.setattr(features, "WindowsSignals", Signals)
    monkeypatch.setattr(features, "host_memory", lambda: (32000, 2000))
    with pytest.raises(RuntimeError, match="headroom"):
        features.preflight("hlg")
    assert features.status("hlg")["qualified"]
    monkeypatch.setattr(features, "host_memory", lambda: (32000, 10000))
    features.preflight("hlg")
    monkeypatch.setenv("SPARK_MEMORY_FLOOR_MB", "11000")
    with pytest.raises(RuntimeError, match="headroom"):
        features.preflight("hlg")


def test_windows_queue_uses_feature_readiness_without_the_full_native_tier(client, monkeypatch):
    monkeypatch.setattr(features, "status", lambda kind: {"available": kind == "hlg", "why": "" if kind == "hlg" else "not qualified"})
    monkeypatch.setattr(server, "WINCUDA", False)
    built = []
    monkeypatch.setattr(server, "build", lambda kind, params, job_id, **kwargs: built.append((kind, job_id)))
    state = client.get("/api/state").json()
    assert state["capabilities"]["hlg"]["available"] and not state["capabilities"]["finish"]["available"]
    response = client.post("/api/jobs", json={"kind": "hlg", "params": {"video": "uploads/clip.mp4"}})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "hlg" and built
    assert client.post("/api/jobs", json={"kind": "finish", "params": {}}).status_code == 501
    assert client.post("/api/jobs", json={"kind": "hlg", "placement": "elsewhere", "params": {}}).status_code == 400
    assert client.post("/api/jobs", json={"kind": "video", "params": {}}).status_code == 501


def test_direct_enqueue_cannot_bypass_feature_validation(client, monkeypatch):
    monkeypatch.setattr(features, "status", lambda kind: {"available": True, "why": ""})
    def reject(*_args, **_kwargs):
        raise server.HTTPException(400, "outside qualified settings")
    monkeypatch.setattr(server, "build", reject)
    with pytest.raises(server.HTTPException, match="outside qualified settings"):
        server.enqueue("finish", "Invalid cut", {})


def test_finish_preview_readiness_cannot_offer_unqualified_local_or_remote_work(client, monkeypatch):
    calls = []
    def ready(fresh=False, local_only=False):
        calls.append(local_only)
        return {"ok": True, "converter": {"ok": True}}
    monkeypatch.setattr(finish, "ready", ready)
    monkeypatch.setattr(finish, "font_files", lambda: {})
    monkeypatch.setattr(features, "status", lambda kind: {"available": False, "why": "needs qualification"})
    response = client.get("/api/finish")
    assert response.status_code == 200
    assert response.json()["ok"] is False and response.json()["why"] == "needs qualification"
    assert calls == [True] and not response.json()["converter"]["ok"]
    assert response.json()["sizes"] == ["720p"] and response.json()["rates"] == [24]


def test_finish_rejects_unqualified_options_and_timeline_lengths(monkeypatch):
    import graphics
    monkeypatch.setattr(graphics, "probe", lambda path: {"width": 1280, "height": 720, "seconds": 10, "fps": 24})
    plan = {"size": "720p", "fps": 24, "clips": [{"length": 6, "path": "clip.mp4"}], "title": {"text": "Title"}}
    features.check_finish(plan)
    for changed in ({"size": "4k"}, {"fps": 60}, {"captions": True}, {"clips": [{"length": 7, "path": "clip.mp4"}]}):
        with pytest.raises(ValueError):
            features.check_finish({**plan, **changed})
    for role in ("song", "voice"):
        with pytest.raises(ValueError, match="music and narration"):
            features.check_finish({**plan, role: {"seconds": 11}})


def test_standalone_finish_uses_windows_runtime_without_overriding_explicit_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(finish.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PREFIX", raising=False)
    assert finish.prefix() == tmp_path / "ImaginationStudio"
    monkeypatch.setenv("PREFIX", str(tmp_path / "explicit-runtime"))
    assert finish.prefix() == tmp_path / "explicit-runtime"


def test_memory_watch_terminates_only_its_owned_job_and_surfaces_the_reason(monkeypatch):
    terminated = threading.Event()
    class Signals:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            return 100, 100000, 0, True
    class Owned:
        def terminate(self):
            terminated.set()
    monkeypatch.setattr(features, "WindowsSignals", Signals)
    with pytest.raises(RuntimeError, match="memory pressure"):
        with features.watch(Owned()):
            assert terminated.wait(2)


def test_resource_refused_recheck_preserves_saved_success(qualification, monkeypatch):
    marker = qualification / "hlg.json"
    saved = json.dumps({"ok": True, "fingerprint": features.fingerprint("hlg")})
    marker.write_text(saved)
    spec = importlib.util.spec_from_file_location("windows_features_check", features.PACK / "scripts" / "windows-features-check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    def busy(kind):
        raise RuntimeError("other applications are using memory")
    monkeypatch.setattr(features, "headroom", busy)
    with pytest.raises(RuntimeError, match="other applications"):
        checker.check("hlg")
    assert marker.read_text() == saved and features.status("hlg")["qualified"]


def test_unsuccessful_rerun_keeps_the_last_successful_evidence(qualification, monkeypatch):
    marker = qualification / "finish.json"
    saved = json.dumps({"ok": True, "fingerprint": features.fingerprint("finish"), "checked_at": "2026-11-04T20:00:00Z"})
    marker.write_text(saved)
    spec = importlib.util.spec_from_file_location("windows_features_check", features.PACK / "scripts" / "windows-features-check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    monkeypatch.setattr(features, "headroom", lambda kind: None)
    def failed(*args, **kwargs):
        raise RuntimeError("the new attempt did not finish")
    monkeypatch.setattr(checker, "run", failed)
    with pytest.raises(RuntimeError, match="did not finish"):
        checker.check("finish")
    assert marker.read_text() == saved and features.status("finish")["qualified"]


@pytest.fixture
def check_ui(client, qualification, monkeypatch):
    class Signals:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            return 1000, 100000, 0, False
    monkeypatch.setattr(features, "WindowsSignals", Signals)
    monkeypatch.setattr(features, "host_memory", lambda: (32000, 8000))
    return client


def test_progressive_status_and_report_never_run_a_test(check_ui, monkeypatch):
    monkeypatch.setattr(server, "enqueue", lambda *_args, **_kwargs: pytest.fail("GET must never run a check"))
    response = check_ui.get("/api/windows/features")
    assert response.status_code == 200, response.text
    cards = response.json()["features"]
    assert [card["id"] for card in cards] == [
        "hlg", "finish", "foley", "scout", "sfx", "graphics",
        "qwen", "qwen-full", "flux3k-fast", "flux3k-full", "moss-image",
        "turnaround", "upscale", "mask", "remove", "swap", "video", "voice", "music"]
    assert all(not card["qualified"] and card["memory"]["minimum_installed_ram_mb"] is None for card in cards)
    assert all(card["memory"]["required_headroom_mb"] == 4096 for card in cards[:2])
    assert cards[2]["memory"]["required_headroom_mb"] == 9216 and cards[2]["check_available"]
    assert not cards[2]["installed"]
    report = check_ui.get("/api/windows/features/report")
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    assert report.json()["features"] == cards
    generic = check_ui.get("/api/feature-checks")
    assert generic.status_code == 200 and generic.json()["features"] == cards
    assert cards[0]["requirements"][0]["pool"] == "host"


def test_progressive_check_is_explicit_queue_work_with_duplicate_refusal(check_ui, monkeypatch):
    created = []
    def enqueue(kind, title, params):
        assert server.lock.acquire(blocking=False), "The handler must release the queue lock before enqueueing."
        server.lock.release()
        created.append((kind, params))
        return {"id": "test-check", "kind": kind, "status": "queued", "params": params}
    monkeypatch.setattr(server, "enqueue", enqueue)
    response = check_ui.post("/api/windows/features/check", json={"feature": "hlg"})
    assert response.status_code == 200, response.text
    assert created == [("qualification", {"feature": "hlg"})]
    monkeypatch.setitem(server.jobs, "check-duplicate", response.json())
    assert check_ui.post("/api/windows/features/check", json={"feature": "hlg"}).status_code == 409
    assert check_ui.post("/api/windows/features/check", json={"feature": "video"}).status_code == 400
    assert check_ui.post("/api/windows/features/check", json={"feature": "finish", "command": "ignored"}).status_code == 400


def test_progressive_mutations_are_local_same_origin_and_native_tier_unchanged(check_ui, monkeypatch):
    assert check_ui.post("/api/windows/features/check", json={"feature": "hlg"},
                         headers={"origin": "http://example.invalid"}).status_code == 403
    assert check_ui.post("/api/windows/features/check", json={"feature": "hlg"},
                         headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert check_ui.post("/api/windows/features/check", content="{}").status_code in (415, 422)
    monkeypatch.setattr(server, "WINDOWS", False)
    monkeypatch.setattr(server, "WIN32", False)
    assert check_ui.get("/api/windows/features").status_code == 501


def test_failed_attempt_does_not_replace_a_saved_success(check_ui, qualification):
    (qualification / "hlg.json").write_text(json.dumps({
        "ok": True, "fingerprint": features.fingerprint("hlg"), "checked_at": "2026-11-04T20:00:00Z",
        "results": [{"peak_job_commit_mb": 2116}],
    }))
    features.save_attempt("hlg", {"ok": False, "error": "temporarily busy"})
    card = check_ui.get("/api/windows/features").json()["features"][0]
    assert card["qualified"] and card["evidence"]["matches"]
    assert card["attempt"]["error"] == "temporarily busy" and card["evidence"]["checked_at"]


def test_render_signatures_ignore_ui_docstrings_and_formatting_but_track_render_dependencies():
    original = 'FACTOR = 2\n\ndef helper():\n    return FACTOR\n\ndef render():\n    """Render a clip."""\n    return helper()\n\ndef panel():\n    return "Old badge"\n'
    cosmetic = original.replace("Old badge", "New badge").replace("Render a clip.", "New documentation.") + "\n# UI update\n"
    signature = features.source_signature(original, ("render",), dependencies=True)
    assert features.source_signature(cosmetic, ("render",), dependencies=True) == signature
    assert features.source_signature(original.replace("FACTOR = 2", "FACTOR = 3"), ("render",), dependencies=True) != signature
    assert features.source_signature(original, exclude=("panel",)) == features.source_signature(cosmetic, exclude=("panel",))


def test_fingerprint_excludes_presentation_and_checker_but_keeps_scope_runtime_and_runner(monkeypatch):
    runtime = {"files": {"python": ["identity", 1, 2]}, "scope": {"seconds": 10}, "host_mb": 32000}
    monkeypatch.setattr(features, "_runtime_fingerprint", lambda kind: {**runtime, "files": dict(runtime["files"])})
    before = features.fingerprint("finish")
    assert "app/server.py" not in before["files"] and "checker" not in before["files"]
    assert "app/hlg.py" not in before["files"] and "app/finish.py" in before["files"]
    runtime["host_mb"] = 31000
    assert features.fingerprint("finish") != before
    runtime["host_mb"] = 32000
    runtime["scope"] = {"seconds": 11}
    assert features.fingerprint("finish") != before
    runtime["scope"] = {"seconds": 10}
    monkeypatch.setattr(features, "RUNNER_CONTRACT", features.RUNNER_CONTRACT + 1)
    assert features.fingerprint("finish") != before


def test_legacy_reindex_requires_exact_proof_and_preserves_live_evidence(qualification, monkeypatch, tmp_path):
    runtime = {"files": {"python": ["identity", 1, 2]}, "scope": {"seconds": 10}, "host_mb": 32000}
    monkeypatch.setattr(features, "_runtime_fingerprint", lambda kind: {**runtime, "files": dict(runtime["files"])})
    proof = tmp_path / "previous.py"
    proof.write_bytes((features.PACK / "app" / "windows_features.py").read_bytes())
    legacy = {**runtime, "files": dict(runtime["files"])}
    for name in ("windows_features.py", "windows_process.py", "windows_memory.py", "windows_media.py",
                 "actions.py", "hlg.py", "server.py", "finish.py", "suite.py"):
        legacy["files"][f"app/{name}"] = hashlib.sha256((features.PACK / "app" / name).read_bytes()).hexdigest()
    legacy["files"]["checker"] = hashlib.sha256((features.PACK / "scripts" / "windows-features-check.py").read_bytes()).hexdigest()
    receipt = {"ok": True, "checked_at": "2026-11-04T20:00:00Z", "results": [{"case": "dialogue", "peak_job_commit_mb": 950}],
               "fingerprint": legacy}
    marker = qualification / "finish.json"
    marker.write_text(json.dumps(receipt))
    changed = json.loads(json.dumps(receipt))
    changed["fingerprint"]["files"]["app/finish.py"] = "different-renderer"
    marker.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="do not exactly match"):
        features.migrate_legacy_receipt("finish", proof)
    assert json.loads(marker.read_text()) == changed
    marker.write_text(json.dumps(receipt))
    updated = features.migrate_legacy_receipt("finish", proof)
    assert updated["checked_at"] == receipt["checked_at"] and updated["results"] == receipt["results"]
    assert updated["previous_fingerprint"] == legacy and updated["fingerprint"] == features.fingerprint("finish")
