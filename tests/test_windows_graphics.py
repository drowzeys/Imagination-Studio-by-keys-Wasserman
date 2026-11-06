"""Windows graphics limits and admission; no browser, GPU or model is started."""
import json
import hashlib
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import graphics
import server


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(graphics, "WINDOWS", True)
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(server.hub, "enabled", False)
    monkeypatch.setattr(graphics, "ready", lambda: {"ok": True, "why": None})
    return TestClient(server.app, base_url="http://127.0.0.1")


def no_media(*args):
    raise AssertionError("This recipe must not read media or start transcription")


def test_default_posix_prefix_from_discovery_does_not_hide_windows_graphics(monkeypatch, tmp_path):
    monkeypatch.setattr(graphics, "WINDOWS", True)
    monkeypatch.delenv("GRAPHICS_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PREFIX", str(tmp_path / "legacy-posix-runtime"))
    assert graphics.install_dir() == tmp_path / "ImaginationStudio" / "graphics"
    monkeypatch.setenv("GRAPHICS_DIR", str(tmp_path / "explicit-graphics"))
    assert graphics.install_dir() == tmp_path / "explicit-graphics"


def test_title_defaults_to_the_qualified_size_and_enforces_exact_bounds(windows):
    made = graphics.plan({"template": "title", "title": "Boundary", "seconds": 10}, no_media)
    assert (made["width"], made["height"], made["duration"], made["data"]["fps"]) == (1280, 720, 10, 24)
    for settings in ({"seconds": 10.001}, {"width": 1282, "height": 720}, {"width": 1280, "height": 722},
                     {"size": "1080p"}, {"size": "vertical"}, {"size": "square"}):
        with pytest.raises(graphics.Refused, match="1280x720 and 10 seconds"):
            graphics.plan({"template": "title", "title": "Outside", **settings}, no_media)
    for template in ("captions",):
        with pytest.raises(graphics.Refused, match="only; no transcription"):
            graphics.plan({"template": template}, no_media)


def test_windows_catalog_and_worker_state_match_admission(windows, monkeypatch):
    state = windows.get("/api/state").json()
    assert state["capabilities"]["graphics"]["available"]
    assert not state["capabilities"]["video"]["available"]
    assert not state["capabilities"]["captions"]["available"]
    assert not state["capabilities"]["workers"]["available"]
    catalog = windows.get("/api/graphics").json()
    assert [t["id"] for t in catalog["templates"]] == list(graphics.WINDOWS_TEMPLATES)
    assert state["capabilities"]["graphics"]["templates"] == list(graphics.WINDOWS_TEMPLATES)
    assert catalog["sizes"] == {"720p": [1280, 720]} and catalog["max_seconds"] == 10
    workers = windows.get("/api/sparks").json()
    assert workers["supported"] is False and workers["workers"] == []
    assert "Windows" in workers["note"] and "this" not in workers
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "order", [])
    for params in ({"template": "captions"}, {"template": "title", "title": "Too long", "seconds": 11}, ["invalid"]):
        response = windows.post("/api/jobs", json={"kind": "graphics", "params": params})
        assert response.status_code == 400
    with pytest.raises(server.HTTPException):
        server.enqueue("graphics", "Not allowed", {"template": "captions"})
    assert not server.jobs
    response = windows.post("/api/jobs", json={"kind": "graphics", "params": {"template": "title", "title": "Allowed", "seconds": 10}})
    assert response.status_code == 200 and response.json()["status"] == "queued"
    assert response.json()["outputs"] == []


@pytest.mark.parametrize("params", [
    {"template": "kinetic", "text": "Words move in time", "song": "song.wav", "beats": [0, 2.7, 5.4, 8.2]},
    {"template": "music-video", "song": "song.wav", "clips": ["clip.mp4"], "seconds": 10, "transition": "cut"},
    {"template": "explainer", "sections": [{"heading": "One"}, {"heading": "Two"}], "seconds_each": 5, "song": "song.wav"},
])
def test_existing_templates_keep_the_same_windows_limits(windows, monkeypatch, tmp_path, params):
    monkeypatch.setattr(graphics, "probe", lambda path: {"seconds": 10, "width": 1280, "height": 720, "audio": True})
    resolve = lambda value, *args: tmp_path / value
    made = graphics.plan(params, resolve)
    assert (made["width"], made["height"], made["duration"], made["data"]["fps"]) == (1280, 720, 10, 24)
    assert made["transcribe"] is None
    with pytest.raises(graphics.Refused, match="1280x720 and 10 seconds"):
        graphics.plan({**params, "size": "1080p"}, resolve)


def test_renderer_is_one_software_worker(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(graphics, "node_bin", lambda: ("node.exe", "22.23.4"))
    command = graphics.command(tmp_path, tmp_path / "out.mp4")
    assert command[-3:] == ["--workers", "1", "--no-browser-gpu"]


def test_readiness_retains_successful_evidence_until_a_compatibility_break(monkeypatch, tmp_path):
    monkeypatch.delenv("STUDIO_QUALIFICATION_STRICT", raising=False)
    monkeypatch.setattr(graphics, "WINDOWS", True)
    monkeypatch.setattr(graphics, "install_dir", lambda: tmp_path)
    for name in ("cli.js", "gsap.min.js"):
        (tmp_path / name).write_text("")
    monkeypatch.setattr(graphics, "node_bin", lambda: ("node.exe", "22.23.4"))
    monkeypatch.setattr(graphics, "installed_version", lambda: graphics.HYPERFRAMES)
    monkeypatch.setattr(graphics, "cli", lambda: tmp_path / "cli.js")
    monkeypatch.setattr(graphics, "gsap_file", lambda: tmp_path / "gsap.min.js")
    monkeypatch.setattr(graphics, "browser", lambda: "edge.exe")
    monkeypatch.setattr(graphics.shutil, "which", lambda *args, **kwargs: "tool.exe")
    monkeypatch.setattr(graphics, "windows_fingerprint", lambda: [{"revision": 1}])
    monkeypatch.setattr(graphics, "windows_probe_error", lambda: "")
    marker = tmp_path / "windows-checked.json"
    assert not graphics.ready()["ok"]
    marker.write_text("[]")
    assert "unreadable" in graphics.ready()["why"]
    marker.write_text(json.dumps({"fingerprint": [{"revision": 1}]}))
    assert "browser-isolation" in graphics.ready()["why"]
    marker.write_text(json.dumps({"fingerprint": [{"revision": 1}], "visible_windows_added": 0}))
    assert "incomplete" in graphics.ready()["why"]
    saved = json.dumps({"fingerprint": [{"revision": 1}], "visible_windows_added": 0,
                        "size": [1280, 720], "fps": 24,
                        "results": [{"template": name} for name in graphics.WINDOWS_TEMPLATES]})
    marker.write_text(saved)
    assert graphics.ready()["ok"]
    monkeypatch.setattr(graphics, "windows_fingerprint", lambda: [{"revision": 2}])
    assert graphics.ready()["ok"] and graphics.ready()["runtime_updated"]
    assert marker.read_text() == saved
    monkeypatch.setenv("STUDIO_QUALIFICATION_STRICT", "1")
    assert "changed" in graphics.ready()["why"]
    monkeypatch.delenv("STUDIO_QUALIFICATION_STRICT")
    monkeypatch.setattr(graphics, "WINDOWS_QUALIFICATION_CONTRACT", 2)
    assert "changed" in graphics.ready()["why"]


def test_unpatched_or_changed_browser_probe_is_refused(monkeypatch, tmp_path):
    source = tmp_path / "probe.js"
    monkeypatch.setattr(graphics, "windows_probe_file", lambda: source)
    assert "unavailable" in graphics.windows_probe_error()
    source.write_bytes(b"unsafe --version")
    assert "compatibility fix" in graphics.windows_probe_error()
    monkeypatch.setattr(graphics, "WINDOWS_PROBE_SHA256", hashlib.sha256(b"headless probe").hexdigest())
    source.write_bytes(b"headless probe")
    assert graphics.windows_probe_error() == ""


def test_windows_probe_uses_headless_puppeteer_and_closes_it_on_errors():
    from test_shell_page import run

    patch = Path(__file__).resolve().parents[1] / "scripts" / "windows-graphics" / "hyperframes-browser.patch"
    body = "\n".join(line[1:] for line in patch.read_text().splitlines() if line.startswith("+") and not line.startswith("+++"))
    body = body.replace('await import("puppeteer-core")', "await loadPuppeteer()")
    out = run("""
      const launches = [], events = []; let os = "win32", broken = false;
      const platform = () => os, FIRST_LAUNCH_TIMEOUT_MS = 1000;
      const extractMajorVersion = s => Number(s.split("/")[1].split(".")[0]);
      const loadPuppeteer = async () => ({ default: { launch: async options => {
        launches.push(options); return { version: async () => { if (broken) throw new Error("probe failed"); return "Chrome/142.0.0"; },
          close: async () => events.push("closed") }; } } });
      async function probe(executablePath, found) {
    """ + body + """
        events.push("unsafe-version");
      }
      out.result = await probe("edge.exe", { ok: true });
      broken = true; try { await probe("edge.exe", { ok: true }); } catch (e) { out.error = e.message; }
      out.launches = launches; out.windowsEvents = events.slice();
      os = "linux"; await probe("chrome", {}); out.linuxEvents = events.slice();
    """)
    assert out["result"] == {"ok": True, "versionMajor": 142}
    assert out["error"] == "probe failed" and out["windowsEvents"] == ["closed", "closed"]
    assert all(p["headless"] and p["executablePath"] == "edge.exe" and p["timeout"] == 1000 for p in out["launches"])
    assert out["linuxEvents"] == ["closed", "closed", "unsafe-version"]


def test_visible_browser_check_uses_executable_identity_not_electrons_window_class(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts" / "windows-graphics-check.py"
    spec = importlib.util.spec_from_file_location("graphics_isolation_check", source)
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    edge = (tmp_path / "msedge.exe").resolve()
    monkeypatch.setattr(check.graphics, "browser", lambda: str(edge))
    monkeypatch.setattr(check, "window_process_path", lambda window: edge if window == 1 else tmp_path / "Code.exe")
    def enumerate_windows(callback, _):
        for window in (1, 2):
            if not callback(window, 0):
                return False
        return True
    def class_name(window, name, size):
        name.value = "Chrome_WidgetWin_1"
        return len(name.value)
    api = SimpleNamespace(EnumWindows=enumerate_windows, IsWindowVisible=lambda handle: True,
                          IsWindow=lambda handle: True, GetClassNameW=class_name)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *args: lambda callback: callback, raising=False)
    assert check.visible_browser_windows() == {1}
    def unreadable(window):
        raise PermissionError("Window owner cannot be inspected")
    monkeypatch.setattr(check, "window_process_path", unreadable)
    with pytest.raises(PermissionError, match="cannot be inspected"):
        check.visible_browser_windows()


def test_windows_media_can_be_trashed_and_restored_with_canonical_paths(windows):
    path = server.HOME / "sound" / "sfx" / "windows-clear-check" / "sound.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    relative = path.relative_to(server.HOME).as_posix()
    response = windows.post("/api/clear", json={"paths": [relative], "films": True})
    assert response.status_code == 200 and response.json()["files"] == 1
    assert not path.exists()
    response = windows.post("/api/trash/restore", json={})
    assert response.status_code == 200 and path.read_bytes() == b"fixture"
