"""Baseline progressive adapters and balanced/strict policy; no model or hardware probes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_windows_ui import client  # noqa: F401
import feature_checks
import windows_baseline_checks as baseline
import windows_checks
import windows_features
import windows_foley
import windows_qualification as policy
import windows_storyboard as image

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("strict", [False, True])
def test_routine_updates_retain_provenance_unless_strict_is_opted_in(monkeypatch, strict):
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", str(int(strict)))
    contract = {"model": "same", "recipe": 1}
    old, new = {"ffmpeg": "old"}, {"ffmpeg": "new"}
    assert feature_checks.matches_qualification(old, new, contract, contract) is not strict
    assert old == {"ffmpeg": "old"}
    assert not feature_checks.matches_qualification(old, old, contract, {**contract, "recipe": 2})


def test_core_gpu_abi_families_ignore_patch_updates_but_not_major_breaks():
    old = policy.gpu_version_contract("3.13.17", {"torch": "2.14.0+cu134", "numpy": "2.4.6"})
    assert policy.gpu_version_contract("3.13.18", {"torch": "2.14.1+cu135", "numpy": "2.4.7"}) == old
    for python, torch, numpy in (("3.14.0", "2.14.0+cu134", "2.4.6"),
                                 ("3.13.17", "3.0.0+cu134", "2.4.6"),
                                 ("3.13.17", "2.14.0+cu140", "2.4.6"),
                                 ("3.13.17", "2.14.0+cu134", "3.0.0")):
        assert policy.gpu_version_contract(python, {"torch": torch, "numpy": numpy}) != old


def test_balanced_cpu_status_keeps_original_evidence_and_checks_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_QUALIFICATION_STRICT", raising=False)
    monkeypatch.setenv("STUDIO_WINDOWS_FEATURE_DIR", str(tmp_path))
    monkeypatch.setattr(windows_features, "prerequisites", lambda kind: "")
    old = {"runner_contract": 1, "machine": "ARM64", "physical_mb": 65536, "host_mb": 32000,
           "scope": {"seconds": 10, "job_commit_mb": 4096}, "python": "3.13.17", "numpy": "2.4.6", "files": {"ffmpeg": "old"}}
    current = copy.deepcopy(old)
    current.update(python="3.13.18", numpy="2.4.7", files={"ffmpeg": "new"})
    monkeypatch.setattr(windows_features, "fingerprint", lambda kind: current)
    marker = tmp_path / "hlg.json"
    original = json.dumps({"ok": True, "fingerprint": old, "checked_at": "2026-11-04T20:00:00Z"})
    marker.write_text(original)
    assert windows_features.status("hlg")["qualified"]
    assert windows_features.status("hlg")["runtime_updated"]
    assert marker.read_text() == original
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    assert not windows_features.status("hlg")["qualified"]
    monkeypatch.delenv("STUDIO_QUALIFICATION_STRICT")
    current["runner_contract"] = 2
    assert not windows_features.status("hlg")["qualified"]


def test_foley_compatibility_ignores_provenance_not_weights_or_recipe():
    old = {"schema": 1, "recipe": {"seconds": 5}, "max_width": 660, "host_required_mb": 9216,
           "physical_mb": 65536, "host_mb": 32000, "dedicated_mb": 31232,
           "weights": {"model": [100, "digest", 1]}, "versions": {"torch": "2.14.0+cu134", "numpy": "2.4.6"},
           "sources": {"renderer": "old"}}
    current = copy.deepcopy(old)
    current["sources"]["renderer"] = "new"
    current["weights"]["model"][2] = 2
    current["versions"]["torch"] = "2.14.1+cu134"
    assert windows_foley.compatibility(old) == windows_foley.compatibility(current)
    current["weights"]["model"][1] = "different-model"
    assert windows_foley.compatibility(old) != windows_foley.compatibility(current)
    current = copy.deepcopy(old)
    current["recipe"]["seconds"] = 10
    assert windows_foley.compatibility(old) != windows_foley.compatibility(current)


def test_model_timestamp_change_checks_contents_without_rendering(tmp_path):
    path = tmp_path / "model"
    path.write_bytes(b"weights")
    files = {"model": (7, hashlib.sha256(b"weights").hexdigest())}
    policy.verify_model_updates(tmp_path, files, {"model": 0})
    before = path.stat()
    path.write_bytes(b"changed")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    assert path.stat().st_mtime_ns != before.st_mtime_ns
    with pytest.raises(RuntimeError, match="SHA-256"):
        policy.verify_model_updates(tmp_path, files, {"model": 0})


@pytest.mark.parametrize("kind", baseline.KINDS)
def test_check_builder_uses_fixed_runner_and_unique_report_paths(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(baseline, "prerequisites", lambda _: "")
    command, env, outputs = windows_checks.build(tmp_path, {"feature": kind}, "test-check")
    assert command[command.index("--feature") + 1] == kind
    assert command[command.index("--report") + 1] == outputs[0]
    assert command[command.index("--output") + 1] == str(tmp_path / "renders" / "calibration" / "test-check")
    assert command[3].endswith("windows-baseline-check.py") and env == {}
    assert windows_checks.gpu_check({"kind": "qualification", "params": {"feature": kind}}) == (kind != "graphics")


def test_existing_receipts_are_displayed_without_a_render_or_rewrite(tmp_path, monkeypatch):
    marker = tmp_path / "qualification.json"
    measured = {"initial": {"gpu_dedicated_total_mb": 8192},
                "minima": {"dedicated_gpu_free_mb": 4096}}
    native = {"result": {"ok": True, "calibration": {"count": 1, "request_to_files_seconds": 2},
                         "memory_guard": measured,
                         "job_commitment": {"peak_job_commit_bytes": 4 << 20, "peak_process_commit_bytes": 3 << 20}}}
    raw = json.dumps(native)
    marker.write_text(raw)
    monkeypatch.setattr(baseline, "receipt_path", lambda _: marker)
    monkeypatch.setattr(windows_features, "saved", lambda *_: {})
    found = baseline.evidence("scout", True)
    assert found["matches"] and found["time_basis"] == "receipt_mtime"
    assert found["results"][0]["case"] == "1 512x512 storyboard drafts"
    assert found["results"][0]["peak_job_commit_mb"] == 4
    assert found["results"][0]["measurements"] == measured
    assert found["results"][0]["rendered"] is True
    assert marker.read_text() == raw


def test_failed_storyboard_rerun_does_not_remove_previous_pass(tmp_path, monkeypatch):
    marker = tmp_path / "qualification.json"
    marker.write_text("original evidence")
    monkeypatch.setattr(image, "folder", lambda: tmp_path)
    monkeypatch.setattr(image, "verify_weights", lambda _: None)
    monkeypatch.setattr(image, "render_case", lambda *_: object())

    def failed(*args, **kwargs):
        raise RuntimeError("cancelled or refused")

    monkeypatch.setattr(image, "tools", lambda: SimpleNamespace(checker=lambda: SimpleNamespace(run_check=failed)))
    with pytest.raises(RuntimeError, match="cancelled or refused"):
        image.render(tmp_path / "new-output", {}, True)
    assert marker.read_text() == "original evidence"


def test_failed_runner_records_attempt_without_replacing_saved_pass(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("baseline_runner", ROOT / "scripts" / "windows-baseline-check.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv("STUDIO_WINDOWS_FEATURE_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    windows_features.save_pass("sfx", {"ok": True, "checked_at": "original"})
    def refused(*_args):
        raise RuntimeError("refused")
    monkeypatch.setattr(runner, "check", refused)
    monkeypatch.setattr(runner.sys, "argv", ["check", "--feature", "sfx", "--output", str(tmp_path / "output"),
                                         "--report", str(tmp_path / "report")])
    assert runner.main() == 1
    assert windows_features.saved("sfx", "")["checked_at"] == "original"
    assert windows_features.saved("sfx", "-attempt")["error"] == "refused"


@pytest.mark.parametrize("kind", baseline.KINDS)
def test_runner_uses_owned_caps_and_publishes_only_completed_checks(tmp_path, monkeypatch, kind):
    from contextlib import nullcontext
    spec = importlib.util.spec_from_file_location("baseline_success_runner", ROOT / "scripts" / "windows-baseline-check.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv("STUDIO_WINDOWS_FEATURE_DIR", str(tmp_path))
    monkeypatch.setattr(baseline, "prerequisites", lambda _: "")
    monkeypatch.setattr(baseline, "status", lambda _: {"qualified": True})
    monkeypatch.setattr(baseline, "evidence", lambda *_: {"matches": True, "results": [], "native_receipt_sha256": "checked"})
    monkeypatch.setattr(windows_features, "headroom", lambda _: None)
    monkeypatch.setattr(windows_features, "watch", lambda _: nullcontext())
    calls = []

    class Owned:
        def __init__(self, *, job_commit_mb):
            calls.append(job_commit_mb)

        def spawn(self, command, **kwargs):
            assert command == baseline.command(kind, tmp_path / "output")
            return SimpleNamespace(wait=lambda timeout: 0)

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(runner, "OwnedJob", Owned)
    assert runner.check(kind, tmp_path / "output")["ok"]
    assert windows_features.saved(kind, "")["native_receipt_sha256"] == "checked"
    assert windows_features.saved(kind, "-attempt")["ok"]
    assert calls == [4096 if kind == "graphics" else 16384, "closed"]


def test_failed_graphics_rerun_does_not_remove_previous_pass(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("graphics_preserve_check", ROOT / "scripts" / "windows-graphics-check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    marker = tmp_path / "windows-checked.json"
    marker.write_text("original evidence")
    monkeypatch.setattr(checker.graphics, "WINDOWS", True)
    monkeypatch.setattr(checker.graphics, "node_bin", lambda: ("node", "current"))
    monkeypatch.setattr(checker.graphics, "browser", lambda: "private-browser")
    monkeypatch.setattr(checker.graphics, "installed_version", lambda: checker.graphics.HYPERFRAMES)
    monkeypatch.setattr(checker.graphics, "windows_probe_error", lambda: "")
    monkeypatch.setattr(checker.graphics, "windows_fingerprint", lambda: ["unchanged"])
    monkeypatch.setattr(checker.graphics, "install_dir", lambda: tmp_path)

    def refused(*args, **kwargs):
        raise RuntimeError("refused")

    monkeypatch.setattr(checker, "run", refused)
    with pytest.raises(RuntimeError, match="refused"):
        checker.check()
    assert marker.read_text() == "original evidence"


@pytest.mark.parametrize("kind", ("scout", "sfx", "foley", "graphics", "hlg", "finish"))
def test_qualification_drains_warm_backend_before_execution(client, monkeypatch, kind):
    from test_windows_ui import server
    order = []
    monkeypatch.setattr(server.windows_storyboard, "release_backend", lambda: order.append("unloaded"))
    monkeypatch.setattr(server.voice, "release", lambda *_: False)
    monkeypatch.setattr(server.music, "release", lambda *_: False)
    monkeypatch.setattr(server.bolt, "admit", lambda *_: "")

    def execute(*args):
        order.append("execute")
        return False, {}, []

    monkeypatch.setattr(server, "take", execute)
    server.perform_job({"id": "test-check", "kind": "qualification", "params": {"feature": kind},
                        "status": "running", "log": ""})
    assert order == ["unloaded", "execute"]
