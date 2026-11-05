"""Desktop backend ownership, idle switching and registrations. No real app, WSL or GPU runs."""
import contextlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import desktop_control

spec = importlib.util.spec_from_file_location("windows_desktop", ROOT / "scripts" / "windows-desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "home", lambda: tmp_path)
    monkeypatch.setattr(desktop, "locked", contextlib.nullcontext)
    return tmp_path


@pytest.fixture
def controlled(monkeypatch):
    monkeypatch.setenv("STUDIO_DESKTOP_TOKEN", "test-private-token")
    monkeypatch.setenv("STUDIO_DESKTOP_SESSION", "test-session")
    monkeypatch.setenv("STUDIO_DESKTOP_BACKEND", "native")
    server = SimpleNamespace(app=FastAPI(), jobs={}, lock=threading.RLock(), stopping=threading.Event())
    shutdown = []
    server.app.state.desktop_shutdown = lambda: shutdown.append(True)
    server.app.include_router(desktop_control.attach(server))
    return server, shutdown


def test_private_control_requires_socket_loopback_and_token_and_no_browser_origin(controlled):
    server, shutdown = controlled
    with TestClient(server.app, client=("127.0.0.1", 5000)) as client:
        assert client.get("/api/desktop/session").status_code == 403
        assert client.post("/api/desktop/stop", headers={"x-studio-desktop": "wrong"}).status_code == 403
        headers = {"x-studio-desktop": "test-private-token"}
        result = client.get("/api/desktop/session", headers=headers)
        assert result.status_code == 200 and result.json()["backend"] == "native"
        assert "test-private-token" not in result.text
        assert client.post("/api/desktop/stop", headers={**headers, "origin": "http://127.0.0.1:7870"}).status_code == 403
    with TestClient(server.app, client=("192.0.2.10", 5000)) as client:
        assert client.post("/api/desktop/stop", headers=headers).status_code == 403
    assert not shutdown and not server.stopping.is_set()


@pytest.mark.parametrize("state", ["queued", "running"])
def test_idle_stop_refuses_queued_or_running_jobs(controlled, state):
    server, shutdown = controlled
    server.jobs["job"] = {"status": state}
    with TestClient(server.app, client=("127.0.0.1", 5000)) as client:
        result = client.post("/api/desktop/stop", headers={"x-studio-desktop": "test-private-token"})
    assert result.status_code == 409 and not shutdown and not server.stopping.is_set()


def test_idle_stop_marks_admission_closed_before_shutdown_callback(controlled):
    server, shutdown = controlled
    server.jobs["job"] = {"status": "done"}
    server.app.state.desktop_shutdown = lambda: shutdown.append(server.stopping.is_set())
    with TestClient(server.app, client=("127.0.0.1", 5000)) as client:
        assert client.post("/api/desktop/stop", headers={"x-studio-desktop": "test-private-token"}).status_code == 200
        assert client.post("/api/desktop/stop", headers={"x-studio-desktop": "test-private-token"}).status_code == 200
    assert shutdown == [True]


def test_drain_failure_keeps_admission_closed_and_can_be_retried(controlled):
    server, shutdown = controlled
    def failed():
        raise RuntimeError("Owned model did not drain")
    server.app.state.desktop_shutdown = failed
    headers = {"x-studio-desktop": "test-private-token"}
    with TestClient(server.app, client=("127.0.0.1", 5000)) as client:
        assert client.post("/api/desktop/stop", headers=headers).status_code == 503
        assert server.stopping.is_set()
        server.app.state.desktop_shutdown = lambda: shutdown.append(True)
        assert client.post("/api/desktop/stop", headers=headers).status_code == 200
    assert shutdown == [True]


def test_unmanaged_app_has_no_desktop_control(monkeypatch):
    monkeypatch.delenv("STUDIO_DESKTOP_TOKEN", raising=False)
    server = SimpleNamespace(app=FastAPI())
    server.app.include_router(desktop_control.attach(server))
    with TestClient(server.app) as client:
        assert client.get("/api/desktop/session").status_code == 404


def test_registration_preserves_both_installations_and_explicit_default(folder, monkeypatch):
    native = folder / "checkout"
    (native / "scripts").mkdir(parents=True)
    (native / "app").mkdir()
    (native / "scripts" / "app.ps1").write_text("test")
    (native / "app" / "desktop_control.py").write_text("test")
    probes = []
    monkeypatch.setattr(desktop, "verify_wsl", lambda found: probes.append(found.copy()))
    first = desktop.register("native", studio=str(native))
    assert first["default"] == "native"
    second = desktop.register("wsl", distro="Ubuntu", user="studio-user", repo="/opt/studio")
    assert set(second["backends"]) == {"native", "wsl"} and second["default"] == "native"
    second["default"] = "wsl"
    desktop.write(folder / "desktop.json", second)
    assert desktop.register("native", studio=str(native))["default"] == "wsl"
    assert len(probes) == 1


@pytest.mark.parametrize("kwargs", [
    {"distro": "-x", "user": "studio", "repo": "/opt/studio"},
    {"distro": "Ubuntu", "user": "", "repo": "/opt/studio"},
    {"distro": "Ubuntu", "user": "studio", "repo": "relative"},
    {"distro": "Ubuntu", "user": "studio", "repo": "/opt/../studio"},
])
def test_invalid_wsl_registration_is_refused(kwargs):
    with pytest.raises(desktop.DesktopError):
        desktop.profile("wsl", **kwargs)


def test_wsl_launch_keeps_credentials_off_argv_and_does_not_use_broad_stop():
    identity = {"token": "private-value", "session": "example-session"}
    command, env, data = desktop.launch_command("wsl", {"distro": "Ubuntu", "user": "studio", "repo": "/opt/studio"}, identity)
    assert command[:7] == ["wsl.exe", "-d", "Ubuntu", "-u", "studio", "--exec", "python3"]
    assert "private-value" not in " ".join(command)
    assert "pkill" not in " ".join(command) and "--terminate" not in command
    settings = json.loads(data)
    assert settings["env"]["STUDIO_DESKTOP_TOKEN"] == "private-value"
    assert settings["env"]["HOST"] == "127.0.0.1"
    assert "scripts/n1x-studio.sh" in command[-1]


def test_legacy_or_foreign_port_is_not_adopted_or_stopped(folder, monkeypatch):
    desktop.write(folder / "desktop-session.json", {"session": "ours", "backend": "native", "token": "example"})
    monkeypatch.setattr(desktop.socket, "create_connection", lambda *args, **kwargs: contextlib.nullcontext())
    calls = []
    def request(path, *args, **kwargs):
        calls.append(path)
        return {"session": "other", "backend": "native", "busy": False}
    monkeypatch.setattr(desktop, "request", request)
    assert desktop.status()["running"] == "external"
    with pytest.raises(desktop.DesktopError, match="outside"):
        desktop.stop_backend()
    assert "/api/desktop/stop" not in calls


def test_switch_waits_for_stop_before_start_and_does_not_change_default(folder, monkeypatch, capsys):
    events = []
    desktop.write(folder / "desktop.json", {"default": "native", "backends": {"native": {}, "wsl": {}}})
    monkeypatch.setattr(desktop, "stop_backend", lambda: events.append("stop/drain"))
    monkeypatch.setattr(desktop, "launch", lambda backend: events.append("start:" + backend))
    monkeypatch.setattr(desktop, "status", lambda: {})
    assert desktop.main(["switch", "--backend", "wsl", "--confirmed"]) == 0
    assert events == ["stop/drain", "start:wsl"]
    assert desktop.read(folder / "desktop.json")["default"] == "native"
    events.clear()
    assert desktop.main(["switch", "--backend", "native"]) == 1
    assert not events


def test_busy_or_failed_stop_never_starts_another_backend(folder, monkeypatch):
    starts = []
    def stop():
        raise desktop.DesktopError("Queue busy or drain failed")
    monkeypatch.setattr(desktop, "stop_backend", stop)
    monkeypatch.setattr(desktop, "launch", starts.append)
    assert desktop.main(["switch", "--backend", "wsl", "--confirmed"]) == 1
    assert not starts


def test_start_shortcut_opens_existing_app_without_adopting_or_launching(folder, monkeypatch):
    monkeypatch.setattr(desktop, "status", lambda: {"running": "external"})
    monkeypatch.setattr(desktop, "launch", lambda *_: pytest.fail("Existing app must not launch again"))
    assert desktop.main(["start"]) == 0


def test_failed_registration_does_not_replace_existing_default(folder, monkeypatch):
    config = {"default": "native", "backends": {"native": {"studio": "existing"}}}
    desktop.write(folder / "desktop.json", config)
    def failed(_):
        raise desktop.DesktopError("WSL files not updated")
    monkeypatch.setattr(desktop, "verify_wsl", failed)
    with pytest.raises(desktop.DesktopError, match="updated"):
        desktop.register("wsl", distro="Ubuntu", user="studio", repo="/opt/studio")
    assert desktop.read(folder / "desktop.json") == config


def test_stop_waits_for_supervisor_to_remove_its_identity(folder, monkeypatch):
    identity = {"session": "ours", "backend": "native", "token": "test"}
    desktop.write(folder / "desktop-session.json", identity)
    monkeypatch.setattr(desktop, "status", lambda: {"running": "native"})
    monkeypatch.setattr(desktop, "owned_status", lambda _: {"busy": False})
    calls = []
    def request(path, received, **kwargs):
        calls.append((path, received, kwargs))
        (folder / "desktop-session.json").unlink()
        return {"stopping": True}
    monkeypatch.setattr(desktop, "request", request)
    desktop.stop_backend()
    assert calls == [("/api/desktop/stop", identity, {"stop": True})]


def test_stop_timeout_never_force_kills_or_releases_identity(folder, monkeypatch):
    identity = {"session": "ours", "backend": "wsl", "token": "test"}
    desktop.write(folder / "desktop-session.json", identity)
    monkeypatch.setattr(desktop, "status", lambda: {"running": "wsl"})
    monkeypatch.setattr(desktop, "owned_status", lambda _: {"busy": False})
    monkeypatch.setattr(desktop, "request", lambda *args, **kwargs: {})
    times = iter((0, 100))
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(times))
    with pytest.raises(desktop.DesktopError, match="not force-killed"):
        desktop.stop_backend()
    assert desktop.read(folder / "desktop-session.json") == identity


def test_wsl_validation_uses_exact_registration_without_shell_interpolation(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(desktop.subprocess, "run", run)
    desktop.verify_wsl({"distro": "Ubuntu", "user": "studio", "repo": "/opt/Studio With Spaces"})
    assert calls[0][0][-1] == "/opt/Studio With Spaces"
    assert calls[0][1]["timeout"] == 30 and calls[0][1]["creationflags"] == desktop.NO_WINDOW


def test_native_start_matches_launch_identity_not_windows_venv_trampoline_pid(folder, monkeypatch):
    desktop.write(folder / "desktop.json", {"default": "native", "backends": {"native": {"studio": "test"}}})
    monkeypatch.setattr(desktop, "available", lambda *args: True)
    monkeypatch.setattr(desktop, "status", lambda: {"running": None})
    def popen(command, **kwargs):
        desktop.write(folder / "desktop-session.json", {
            "session": kwargs["env"]["STUDIO_DESKTOP_LAUNCH"], "supervisor": 222, "backend": "native", "token": "test"})
        return SimpleNamespace(pid=111, poll=lambda: None)
    monkeypatch.setattr(desktop.subprocess, "Popen", popen)
    matched = []
    monkeypatch.setattr(desktop, "owned_status", lambda identity: matched.append(identity["supervisor"]) or {})
    desktop.launch("native")
    assert matched == [222]

def test_stop_rechecks_busy_with_private_identity(folder, monkeypatch):
    desktop.write(folder / "desktop-session.json", {"session": "ours", "backend": "native", "token": "example"})
    monkeypatch.setattr(desktop, "status", lambda: {"running": "native"})
    monkeypatch.setattr(desktop, "owned_status", lambda _: {"busy": True})
    monkeypatch.setattr(desktop, "request", lambda *args, **kwargs: pytest.fail("Busy app must not be stopped"))
    with pytest.raises(desktop.DesktopError, match="queued"):
        desktop.stop_backend()


def test_wsl_menu_refresh_does_not_launch_distro_or_touch_unc(folder, monkeypatch):
    desktop.write(folder / "desktop.json", {"default": "wsl", "backends": {"wsl": {
        "distro": "Ubuntu", "user": "studio", "repo": "/opt/studio", "verified": True}}})
    def absent(*args, **kwargs):
        raise ConnectionRefusedError
    monkeypatch.setattr(desktop.socket, "create_connection", absent)
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: pytest.fail("Menu must not start WSL"))
    assert desktop.status()["backends"]["wsl"]["available"]


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell parser required")
def test_tray_and_registration_scripts_parse_without_launching():
    for name in ("studio-tray.ps1", "setup-windows-desktop.ps1"):
        path = str(ROOT / "scripts" / name).replace("'", "''")
        command = f"$errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$errors); if($errors){{$errors;exit 1}}"
        done = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell is required")
def test_tray_passes_all_operation_arguments_and_closures():
    path = str(ROOT / "scripts" / "studio-tray.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
$tree=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$null)
$node=$tree.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Run-Desktop'}},$true)
Invoke-Expression $node.Extent.Text
$global:Received=@()
function Invoke-Desktop([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments) {{
  $global:Received=$Arguments; return @{{ok=$true}}
}}
function Refresh-Desktop {{}}
function Show-Problem($Message) {{throw $Message}}
$Tray=[pscustomobject]@{{Text=''}}
$script:Busy=$false
$Value='wsl'
$handler={{Run-Desktop @('switch','--backend',$Value,'--confirmed')}}.GetNewClosure()
& $handler
if (($global:Received -join ' ') -ne 'switch --backend wsl --confirmed') {{throw ($global:Received -join ',')}}
"""
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(not shutil.which("powershell"), reason="WinForms on Windows is required")
def test_actual_winforms_menu_has_independent_defaults_and_bound_switch_actions():
    path = str(ROOT / "scripts" / "studio-tray.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$tree=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$null)
$names=@('Add-Menu','Draw-Menu')
$tree.FindAll({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names}},$true) |
  ForEach-Object {{Invoke-Expression $_.Extent.Text}}
$Menu=New-Object System.Windows.Forms.ContextMenuStrip
$Tray=[pscustomobject]@{{Text=''}}
$script:Snapshot=[pscustomobject]@{{running='native';default='wsl';detail='Ready';backends=@{{
  native=@{{available=$true}};wsl=@{{available=$true}}
}}}}
$script:Busy=$false
$script:RefreshError=''
function Refresh-Desktop {{}}
function Confirm-Stop {{return $true}}
function Run-Desktop([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments,[switch]$Open) {{
  $global:Sent=$Arguments
}}
try {{
  Draw-Menu
  $default=@($Menu.Items | Where-Object Text -eq 'Default: WSL2')
  if ($default.Count -ne 1 -or -not $default[0].Checked) {{throw 'Default not checked'}}
  $switch=@($Menu.Items | Where-Object Text -eq 'Switch to WSL2')[0]
  if (-not $switch.Enabled) {{throw 'Installed WSL switch disabled'}}
  $switch.PerformClick()
  if (($global:Sent -join ' ') -ne 'switch --backend wsl --confirmed') {{throw 'Wrong switch closure'}}
  $script:Snapshot.running='external'
  Draw-Menu
  if (@($Menu.Items | Where-Object Text -eq 'Stop Studio')[0].Enabled) {{throw 'External app must not be stoppable'}}
}} finally {{$Menu.Dispose()}}
"""
    done = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
