"""The status bar's readings: python3 -m pytest tests/test_telemetry.py. No GPU and no network; nvidia-smi is stood in
for where a test needs it."""
import ast
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import telemetry  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


def test_a_gb10_line_parses():
    """What nvidia-smi printed on the production Spark (GB10) for the fields asked."""

    one = telemetry.parse_gpu("0, 43, 12.27, 2411, NVIDIA GB10")
    assert one == {"name": "NVIDIA GB10", "util_pct": 0.0, "temp_c": 43.0, "power_w": 12.27, "clock_mhz": 2411}
    assert telemetry.parse_gpu("[N/A], 40, [N/A], 2000, NVIDIA GB10")["util_pct"] is None
    assert telemetry.parse_gpu("garbage") is None


def test_vm_stat_parses():
    text = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1000.\nPages active: 5000.\n"
            "Pages inactive: 2000.\nPages speculative: 100.\nPages purgeable: 0.\n")
    assert telemetry.parse_vm_stat(text, 36864) == 3100 * 16384 // 2 ** 20  # an older vm_stat: as before
    # with the file-backed line, inactive anonymous pages are not available: the figures at the 2026-10-20 panic
    panic = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 926.\nPages active: 3736622.\n"
             "Pages inactive: 3735636.\nPages speculative: 986.\nPages purgeable: 0.\nFile-backed pages: 6604.\n")
    assert telemetry.parse_vm_stat(panic, 262144) == (926 + 6604) * 16384 // 2 ** 20  # 117 MB, not 57 GB


def test_the_snapshot_answers_fast_with_every_field():
    client.get("/api/telemetry")
    started = time.time()
    now = client.get("/api/telemetry").json()
    assert time.time() - started < 0.15
    for key in ("time", "host", "platform", "gpu", "memory", "cpu", "disk", "gpu_lock", "renderer", "jobs", "governor", "warnings"):
        assert key in now, key
    memory_keys = {"total_mb", "available_mb", "floor_mb", "used_pct"}
    card = server.memory_guard.device(fresh=True) if server.SPARK else None
    if card:
        memory_keys.add("card")
    assert set(now["memory"]) == memory_keys
    if card:
        assert set(now["memory"]["card"]) == {"total_mb", "used_mb", "free_mb", "simulated"}
        assert now["memory"]["card"]["total_mb"] == card["total_mb"]
    assert now["disk"]["path"] == str(server.HOME) and now["disk"]["total_gb"] > 0
    assert now["renderer"]["comfy"]["port"] and now["jobs"]["current"] is None or isinstance(now["jobs"]["current"], dict)


def test_the_gpu_lock_holder_is_read():
    folder = Path(os.environ["STUDIO_GPU_LOCK"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "owner").write_text("studio-voice 1017-120000-abcdef 4242\n")
    try:
        time.sleep(0.01)
        lock = client.get("/api/telemetry").json()["gpu_lock"]
        assert lock == {"held": True, "holder": "studio-voice 1017-120000-abcdef 4242"}
    finally:
        (folder / "owner").unlink()
        folder.rmdir()
    assert client.get("/api/telemetry").json()["gpu_lock"] == {"held": False, "holder": None}


def test_a_running_job_is_the_current_one():
    job = {"id": "t-running", "kind": "video", "status": "running", "title": "A test clip", "params": {}, "progress": {"stage": "picture", "at": 3, "of": 8},
           "estimate": 120, "started": time.time() - 5, "log": "", "outputs": []}
    with server.lock:
        server.jobs[job["id"]] = job
        server.order.append(job["id"])
    try:
        current = client.get("/api/telemetry").json()["jobs"]["current"]
        assert current["id"] == "t-running" and current["progress"]["at"] == 3 and 4 < current["elapsed"] < 30
    finally:
        with server.lock:
            server.jobs.pop(job["id"])
            server.order.remove(job["id"])


def test_a_card_under_its_floor_does_not_say_the_render_would_be_stopped():
    """GPU free under 2 GB is the card's own warning. The guard beside ComfyUI still stops on system RAM,
    so this line must not say the render would be stopped, and a tight card must not fall through to that RAM line."""

    tight = telemetry.low_memory_warning({"card": {"free_mb": 500}, "available_mb": 1000, "floor_mb": 8000}, 2048)
    assert tight == "GPU memory (0.5 GB) is below the memory guard's floor (2.0 GB)."
    assert "stopped" not in tight
    assert telemetry.low_memory_warning({"card": {"free_mb": 40000}, "available_mb": 1000, "floor_mb": 8000}, 2048) is None
    spark = telemetry.low_memory_warning({"available_mb": 1000, "floor_mb": 8000}, 2048)
    assert spark == "Available memory (1.0 GB) is below the memory guard's floor (8.0 GB): a render now would be stopped."


def test_warnings_in_plain_words(monkeypatch):
    hot = {"name": "NVIDIA GB10", "util_pct": 99.0, "temp_c": 91.0, "power_w": 80.0, "clock_mhz": 2400}
    monkeypatch.setattr(telemetry.shutil, "which", lambda name, *a, **k: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    real = telemetry.subprocess.run

    def stand_in(command, *args, **kwargs):
        if command[0] == "nvidia-smi":
            assert kwargs.get("timeout")
            return subprocess.CompletedProcess(command, 0, "99, 91, 80.0, 2400, NVIDIA GB10\n", "")
        return real(command, *args, **kwargs)

    monkeypatch.setattr(telemetry.subprocess, "run", stand_in)
    monkeypatch.setattr(telemetry, "GPU_FRESH", telemetry.FRESH)  # the GPU's answer is kept 5 s in use
    time.sleep(telemetry.FRESH + 0.05)
    now = client.get("/api/telemetry").json()
    assert now["gpu"] == hot
    assert any("91 °C" in line for line in now["warnings"])


def test_history_is_filled_by_the_sampler():
    deadline = time.time() + 5
    while time.time() < deadline and not client.get("/api/telemetry/history").json()["samples"]:
        time.sleep(0.2)
    samples = client.get("/api/telemetry/history").json()["samples"]
    assert samples and set(samples[0]) == {"t", "gpu", "mem", "temp", "power"}


def test_storage_is_measured_in_the_background():
    (server.HOME / "renders" / "a.mp4").write_bytes(b"x" * 5000)
    first = client.get("/api/storage").json()
    assert {"home", "total_gb", "free_gb", "used_pct", "buckets", "computing", "computed_at"} <= set(first)
    assert client.post("/api/storage/refresh", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/storage/refresh").status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and client.get("/api/storage").json()["computing"]:
        time.sleep(0.1)
    answer = client.get("/api/storage").json()
    renders = next(one for one in answer["buckets"] if one["id"] == "renders")
    assert renders["files"] >= 1 and answer["computed_at"]
    assert {one["id"] for one in answer["buckets"]} >= {"renders", "scouts", "trash", "other"}
    assert not [route for route in server.app.routes if getattr(route, "path", "").startswith("/api/storage") and "DELETE" in getattr(route, "methods", ())]


def test_the_event_stream_sends_telemetry_and_ends_on_disconnect():
    """A real server: the stream is endless, so it is read a few events in and the connection is dropped."""

    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    web = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not web.started and time.time() < deadline:
            time.sleep(0.05)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/api/events")
        answer = connection.getresponse()
        assert answer.status == 200 and answer.getheader("content-type").startswith("text/event-stream")
        seen = b""
        while b"event: jobs" not in seen or b"event: telemetry" not in seen:
            seen += answer.read1(65536)
        block = seen.split(b"event: telemetry\ndata: ", 1)[1].split(b"\n\n", 1)[0]
        assert "memory" in json.loads(block)
        json.loads(seen.split(b"event: jobs\ndata: ", 1)[1].split(b"\n\n", 1)[0])
        connection.close()
    finally:
        web.should_exit = True
        thread.join(10)
    assert not thread.is_alive()  # the stream ended with its client: the server could stop


def test_no_tool_runs_without_a_time_limit():
    for name in ("telemetry.py", "suite.py"):
        tree = ast.parse((ROOT / "app" / name).read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("subprocess.")]
        assert all(any(keyword.arg == "timeout" for keyword in call.keywords) for call in calls), name


def test_the_gpu_is_asked_less_while_something_renders_and_the_governor_only_where_it_is_named():
    assert telemetry.GPU_BUSY > telemetry.GPU_FRESH >= telemetry.TICK and telemetry.SAMPLE >= 10
    assert "model_vault" not in str(telemetry.GOVERNOR)  # no machine's own folder built in


def test_the_disk_reads_as_df_shows_it(tmp_path):
    import subprocess as sp
    mine = telemetry.disk_of(tmp_path)
    line = sp.run(["df", "-k", str(tmp_path)], capture_output=True, text=True).stdout.splitlines()[-1].split()
    used_k, avail_k = int(line[2]), int(line[3])
    assert abs(mine["used_pct"] - 100 * used_k / (used_k + avail_k)) < 1.0  # df rounds its own Use% up
