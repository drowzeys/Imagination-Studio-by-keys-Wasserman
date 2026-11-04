"""Offline install audits use cached files and metadata, never hardware or downloads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_install_check", ROOT / "scripts" / "windows-install-check.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def test_dependency_probe_reads_metadata_without_importing_gpu_libraries():
    command = [sys.executable, "-I", "-c",
               install.METADATA + "\nassert not {'torch', 'torchvision', 'torchaudio', 'server'} & set(sys.modules)",
               json.dumps({"pip": "unused"})]
    done = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(done.stdout)["versions"]["pip"]


def test_dependency_check_requires_exact_pins_native_gpu_python_and_isolation(tmp_path, monkeypatch):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("# pinned\n--extra-index-url https://example.invalid/packages\ntorch==2.14.0+cu134\n")
    answer = {"platform": "win32", "python": [3, 13], "machine": "ARM64", "isolated": True,
              "versions": {"torch": "2.14.0+cu134"}}
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(answer))

    monkeypatch.setattr(install.subprocess, "run", run)
    result = install.dependencies(tmp_path / "python.exe", manifest, arm64=True)
    assert result["packages"] == 1
    assert [c[0][-2:] for c in calls if c[0][-2:] == ["pip", "check"]] == [["pip", "check"]]
    assert all(c[1]["timeout"] == 30 and c[1]["check"] for c in calls)
    answer["versions"]["torch"] = "2.14.0"
    with pytest.raises(RuntimeError, match="expected 2.14.0\\+cu134"):
        install.dependencies(tmp_path / "python.exe", manifest, arm64=True)
    answer["versions"]["torch"] = "2.14.0+cu134"
    answer["machine"] = "AMD64"
    with pytest.raises(RuntimeError, match="native ARM64"):
        install.dependencies(tmp_path / "python.exe", manifest, arm64=True)
    answer["isolated"] = False
    with pytest.raises(RuntimeError, match="isolated Windows Python"):
        install.dependencies(tmp_path / "python.exe", manifest, arm64=False)
    manifest.write_text("torch>=2\n")
    with pytest.raises(ValueError, match="exact package pin"):
        install.pinned_requirements(manifest)


def test_cached_storyboard_checks_hashes_without_download_or_qualification(tmp_path, monkeypatch):
    import urllib.request
    import windows_storyboard
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("No network in offline checks"))
    content = b"cached weight"
    monkeypatch.setattr(windows_storyboard, "FILES", {"weight.safetensors": (len(content), hashlib.sha256(content).hexdigest())})
    monkeypatch.setattr(windows_storyboard, "folder", lambda: tmp_path)
    monkeypatch.setattr(windows_storyboard, "status", lambda: {"available": False, "why": "Not qualified"})
    (tmp_path / "weight.safetensors").write_bytes(content)
    (tmp_path / "LICENSE.md").write_text("fixture licence")
    (tmp_path / "model_index.json").write_text("{}")
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    result = install.audit(["storyboard"], tmp_path)
    assert result["ok"] and result["offline"]
    assert not result["components"][0]["qualification"]["available"]
    assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before
    (tmp_path / "weight.safetensors").write_bytes(b"x" * len(content))
    result = install.audit(["storyboard"], tmp_path)
    assert not result["ok"] and "SHA-256" in result["components"][0]["error"]


def test_missing_components_fail_independently_and_do_not_create_install_folders(tmp_path, monkeypatch):
    import windows_media
    import windows_sound
    monkeypatch.setattr(windows_media, "folder", lambda: tmp_path / "media")
    monkeypatch.setattr(windows_sound, "folder", lambda: tmp_path / "sound")
    result = install.audit(["brain", "sound", "media", "sound"], tmp_path)
    assert not result["ok"] and len(result["components"]) == 3
    assert all(not c["ok"] and c["error"] for c in result["components"])
    assert not list(tmp_path.iterdir())


def test_comfy_checks_source_without_startup(tmp_path, monkeypatch):
    import windows_storyboard
    checked = []
    monkeypatch.setattr(install, "dependencies", lambda *a, **k: {"packages": 86, "scope": "metadata"})
    monkeypatch.setattr(windows_storyboard, "tools", lambda: SimpleNamespace(
        checker=lambda: SimpleNamespace(verify_source=lambda source: checked.append(source))))
    assert install.check("comfy", tmp_path)["packages"] == 86
    assert checked == [tmp_path / "ComfyUI"]


def test_graphics_checks_existing_receipt_and_pins_without_launching_browser(tmp_path, monkeypatch):
    import graphics
    import windows_media
    monkeypatch.setattr(graphics, "ready", lambda: {"ok": True, "node_version": "22.23.4", "hyperframes": graphics.HYPERFRAMES})
    monkeypatch.setattr(graphics, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(windows_media, "search_path", lambda path: path)
    package = tmp_path / "node_modules" / "gsap" / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": graphics.GSAP}))
    lock = ROOT / "scripts" / "windows-graphics" / "package-lock.json"
    (tmp_path / "package-lock.json").write_bytes(lock.read_bytes())
    assert install.audit(["graphics"], tmp_path)["ok"]
    (tmp_path / "package-lock.json").write_text("{}")
    result = install.audit(["graphics"], tmp_path)
    assert not result["ok"] and "lockfile differs" in result["components"][0]["error"]
    monkeypatch.setattr(graphics, "ready", lambda: {"ok": False, "why": "Browser patch missing"})
    result = install.audit(["graphics"], tmp_path)
    assert "Browser patch missing" in result["components"][0]["error"]


def test_a_dependency_subprocess_failure_is_reported_with_its_actual_error(tmp_path, monkeypatch):
    def fail(*args):
        raise subprocess.CalledProcessError(1, ["python", "-m", "pip", "check"], "package conflict", "")
    monkeypatch.setattr(install, "check", fail)
    result = install.audit(["ui", "gpu"], tmp_path)
    assert not result["ok"] and all(row["error"] == "package conflict" for row in result["components"])


def test_main_checks_ui_only_by_default_and_returns_failure_without_repairing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    checked = []
    monkeypatch.setattr(install, "check", lambda name, root: checked.append((name, root)) or {"scope": "fixture"})
    assert install.main([]) == 0
    assert checked == [("ui", tmp_path / "ImaginationStudio")]
    assert json.loads(capsys.readouterr().out)["offline"]
    monkeypatch.setattr(install, "check", lambda *a: (_ for _ in ()).throw(RuntimeError("Missing files")))
    assert install.main(["--components", "ui", "gpu"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
