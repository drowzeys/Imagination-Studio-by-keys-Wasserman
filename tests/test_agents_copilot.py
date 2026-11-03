"""Copilot CLI protocol, Windows admission and handle-owned cleanup; no model/network."""
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "Studio"))
sys.path.insert(0, str(ROOT / "app"))
import agents
import server
from fastapi.testclient import TestClient


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "HOME", tmp_path)
    monkeypatch.setattr(agents, "tasks", {})
    monkeypatch.setattr(agents, "threads", {})
    monkeypatch.setattr(agents, "pending", queue.Queue())
    monkeypatch.setattr(agents, "running", {"id": None, "process": None, "remote": None})
    monkeypatch.setattr(agents, "ensure_worker", lambda: None)
    monkeypatch.setattr(agents, "version_key", lambda path: (1, 0, 105))
    return tmp_path


def test_copilot_harness_is_limited_to_selected_mcp_servers(isolated):
    session = str(uuid.uuid4())
    task = {"backend": "copilot", "thread": "th-test", "_session": {"id": session}}
    args = agents.harness_args(task, "copilot", "mcp.json", {"studio": {}}, "instructions")
    assert args[args.index("--available-tools") + 1] == "studio"
    assert args[args.index("--allow-tool") + 1] == "studio"
    assert args[args.index("--additional-mcp-config") + 1] == "@mcp.json"
    assert args[args.index("--session-id") + 1] == session
    assert "--disable-builtin-mcps" in args and "--no-custom-instructions" in args
    assert "--allow-all-tools" not in args and "--allow-all-paths" not in args
    assert "-p" not in args and "instructions" not in args


def test_copilot_without_mcp_can_only_report_intent(isolated):
    args = agents.harness_args({"backend": "copilot"}, "copilot", "mcp.json", {}, "")
    assert args[-2:] == ["--available-tools", "report_intent"]
    assert "--allow-tool" not in args


def test_old_copilot_is_refused_explicitly(monkeypatch):
    monkeypatch.setattr(agents, "version_key", lambda path: (0, 0, 423))
    with pytest.raises(RuntimeError, match="1.0.105"):
        agents.copilot_version("copilot")


def test_windows_install_plan_gives_a_terminal_command_without_running_a_shell(monkeypatch):
    monkeypatch.setattr(agents, "WINDOWS", True)
    monkeypatch.setattr(agents, "shell_here", lambda *args: pytest.fail("must not start a POSIX installer"))
    plan = agents.install_plan("copilot")
    assert not plan["ok"] and "winget install GitHub.Copilot" in plan["reason"]


def test_copilot_vision_is_not_run_with_claude_arguments(monkeypatch, tmp_path):
    import qa
    tried = []
    monkeypatch.setattr(qa, "default_brain", lambda: {"backend": "copilot"})
    monkeypatch.setattr(qa, "tool", lambda name: tried.append(name))
    monkeypatch.setattr(qa, "bolt_engine", lambda endpoint: None)
    monkeypatch.setattr(qa, "local_vision", lambda *args: "A frame.")
    answer, who = qa.look(tmp_path / "frame.png", {"endpoint": "http://127.0.0.1:9999/v1"})
    assert answer == "A frame." and who == "local model"
    assert tried == ["claude", "codex"]


def test_jsonl_reply_tools_jobs_and_errors_are_not_success_fallbacks():
    task = {"reply": "", "_host": "", "_session": {"id": ""}}
    session = str(uuid.uuid4())
    def event(kind, data):
        return agents.render("copilot", json.dumps({"type": kind, "data": data}), task)
    assert "studio" in event("tool.execution_start", {"toolName": "studio-queue_still"})
    event("tool.execution_complete", {"success": True, "result": {"content": json.dumps(
        {"id": "test-job", "kind": "scout", "status": "queued", "outputs": []})}})
    event("assistant.message", {"content": "Plan café."})
    agents.render("copilot", json.dumps({"type": "result", "exitCode": 0, "sessionId": session}), task)
    assert task["reply"] == "Plan café." and task["jobs"] == ["test-job"]
    assert task["_session"]["id"] == session
    event("session.error", {"message": "No authentication information found"})
    assert "copilot login" in task["_error"]
    agents.render("copilot", json.dumps({"type": "result", "exitCode": 1}), task)
    assert task["_error"]


def test_windows_resolves_the_native_npm_executable(monkeypatch, tmp_path):
    launcher = tmp_path / "copilot.cmd"
    native = tmp_path / "node_modules" / "@github" / "copilot" / "node_modules" / "@github" / "copilot-win32-arm64" / "copilot.exe"
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.setattr(agents, "WINDOWS", True)
    monkeypatch.setattr(agents.shutil, "which", lambda *args, **kwargs: str(launcher))
    monkeypatch.setattr(agents.platform, "machine", lambda: "ARM64")
    assert agents.cli("copilot") == str(native)
    native.unlink()
    assert agents.cli("copilot") is None


@pytest.mark.parametrize("backend", ["claude", "codex", "local", "hermes"])
def test_windows_gate_rejects_other_backends_before_model_or_queue_work(isolated, monkeypatch, backend):
    monkeypatch.setattr(server, "WINDOWS", True)
    monkeypatch.setattr(agents, "WINDOWS", True)
    before = len(server.jobs)
    response = TestClient(server.app).post("/api/agent/tasks", json={"backend": backend, "prompt": "A test", "tools": []})
    assert response.status_code == 501
    assert agents.pending.empty() and not agents.tasks and len(server.jobs) == before


def test_copilot_task_round_trip_keeps_session_and_posix_outputs(isolated, monkeypatch):
    fake = isolated / "fake-cli.py"
    fake.write_text("""
import json, pathlib, sys
args = sys.argv[1:]
message = sys.stdin.read()
session = args[args.index('--session-id') + 1]
log = pathlib.Path('received.jsonl')
with log.open('a') as out:
    out.write(json.dumps({'session': session, 'message': message}) + '\\n')
pathlib.Path('board').mkdir(exist_ok=True)
pathlib.Path('board/frame.png').write_bytes(b'fixture')
print(json.dumps({'type': 'assistant.message', 'data': {'content': 'A plan.'}}))
print(json.dumps({'type': 'result', 'exitCode': 0, 'sessionId': session}))
""")
    original = agents.harness_args
    monkeypatch.setattr(agents, "cli", lambda name: sys.executable)
    monkeypatch.setattr(agents, "harness_args", lambda *args, **kwargs: [sys.executable, str(fake), *original(*args, **kwargs)[1:]])
    monkeypatch.setattr(agents, "WINDOWS", True)
    first = agents.submit({"backend": "copilot", "prompt": "Plan a shot.", "tools": ["studio"]})
    agents.perform(agents.tasks[first["id"]])
    agents.tasks[first["id"]]["outputs"] = agents.outputs_of(agents.tasks[first["id"]])
    agents.finish_turn(agents.tasks[first["id"]])
    thread = agents.threads[first["thread"]]
    assert agents.tasks[first["id"]]["status"] == "done"
    session = thread["session"]["id"]
    second = agents.submit({"thread": thread["id"], "prompt": "Keep the plan.", "tools": ["studio"]})
    agents.perform(agents.tasks[second["id"]])
    agents.tasks[second["id"]]["outputs"] = agents.outputs_of(agents.tasks[second["id"]])
    agents.finish_turn(agents.tasks[second["id"]])
    assert agents.tasks[second["id"]]["status"] == "done"
    assert thread["session"]["id"] == session
    assert any(p.endswith("/board/frame.png") for p in agents.tasks[first["id"]]["outputs"])
    assert all("\\" not in p for p in agents.tasks[first["id"]]["outputs"])
    received = [json.loads(line) for line in (agents.work_folder(agents.tasks[first["id"]]) / "received.jsonl").read_text().splitlines()]
    assert len(received) == 2 and all(row["session"] == session for row in received)
    assert "Plan a shot." in received[0]["message"] and received[1]["message"] == "Keep the plan."


def test_cancelled_first_turn_does_not_publish_a_successful_session(isolated, monkeypatch):
    fake = isolated / "slow-cli.py"
    fake.write_text("import sys,time; sys.stdin.read(); time.sleep(30)")
    original = agents.harness_args
    monkeypatch.setattr(agents, "cli", lambda name: sys.executable)
    monkeypatch.setattr(agents, "harness_args", lambda *args, **kwargs: [sys.executable, str(fake), *original(*args, **kwargs)[1:]])
    monkeypatch.setattr(agents, "WINDOWS", True)
    started = agents.submit({"backend": "copilot", "prompt": "A test.", "tools": ["studio"]})
    task = agents.tasks[started["id"]]
    runner = threading.Thread(target=agents.perform, args=(task,))
    runner.start()
    try:
        deadline = time.monotonic() + 10
        while not agents.running.get("process") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert agents.running.get("process")
        agents.cancel(task["id"])
        runner.join(10)
        assert not runner.is_alive()
        agents.finish_turn(task)
        assert task["status"] == "cancelled"
        assert agents.threads[task["thread"]]["session"] is None
    finally:
        if runner.is_alive():
            agents.stop(agents.running["process"])
            runner.join(10)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-owned process cleanup")
def test_windows_spawn_owns_descendants_before_they_run():
    from windows_process import OwnedJob
    job = OwnedJob()
    child_handle = None
    process = None
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    api.WaitForSingleObject.restype = ctypes.c_ulong
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    try:
        process = job.spawn([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE, text=True)
        child_pid = int(process.stdout.readline())
        child_handle = api.OpenProcess(0x100000, False, child_pid)
        assert child_handle
        agents.stop(process)
        assert api.WaitForSingleObject(child_handle, 5000) == 0
        assert job.handle is None
    finally:
        job.close()
        if process:
            process.wait(timeout=10)
        if child_handle:
            api.CloseHandle(child_handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-owned process cleanup")
def test_one_shot_cli_timeout_drains_its_owned_job(monkeypatch):
    import windows_process
    original, jobs = windows_process.OwnedJob, []
    def owned():
        job = original()
        jobs.append(job)
        return job
    monkeypatch.setattr(windows_process, "OwnedJob", owned)
    with pytest.raises(subprocess.TimeoutExpired):
        agents.run_cli([sys.executable, "-c", "import time; time.sleep(30)"], "", "", 0.2)
    assert len(jobs) == 1 and jobs[0].handle is None
