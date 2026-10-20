"""NVIDIA Nemotron 3.5 Bolt as the Studio's own engine (app/bolt.py): python3 -m pytest tests/test_bolt.py. No GPU, no
model, no network: the engine is a stand-in, a small OpenAI-compatible HTTP server on a loopback port that answers
/health after a short load, lists one model and answers chat completions (slowly when asked)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
os.environ["STUDIO_GPU_LOCK"] = str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d")
os.environ.setdefault("BOLT_BACKEND", "external")  # nothing of this machine's is managed unless a test says so
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import agents  # noqa: E402
import bolt  # noqa: E402
import slate  # noqa: E402
import voice  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)

FAKE = r'''
import json, os, sys, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
port = int(sys.argv[sys.argv.index("--port") + 1])
began, load_s = time.time(), float(os.environ.get("FAKE_LOAD_S", "0.3"))
log = os.environ.get("FAKE_LOG")
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health":
            return self.send(200 if time.time() - began >= load_s else 503, {"status": "ok"})
        if self.path == "/v1/models":
            return self.send(200, {"data": [{"id": "nvidia-nemotron-3.5-bolt"}]})
        self.send(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = json.dumps(body["messages"])
        if log:
            open(log, "a").write("asked\n")
        if "slowly" in text:
            time.sleep(1.5)
        self.send(200, {"choices": [{"message": {"role": "assistant", "content": "ready"}, "finish_reason": "stop"}]})
print("[fake bolt] listening on", port, file=sys.stderr, flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


class Fake(bolt.Engine):
    """An Engine whose server is the stand-in above."""

    name, label = "fake", "stand-in · tests"
    load_s, NEED_GB, RESIDENT_GB = 1.0, 1.0, 28.0

    def __init__(self, folder: Path, load_s: float = 0.3):
        super().__init__()
        self.script = folder / "fake_bolt.py"
        self.script.write_text(FAKE)
        self.fake_load = load_s
        self.asked = folder / "asked.log"

    def present(self) -> str:
        return ""

    def argv(self, port: int) -> list[str]:
        return [sys.executable, str(self.script), "--port", str(port)]

    def environment(self) -> dict:
        return {**os.environ, "FAKE_LOAD_S": str(self.fake_load), "FAKE_LOG": str(self.asked)}


@pytest.fixture
def fake(monkeypatch):
    folder = Path(tempfile.mkdtemp())
    one = Fake(folder)
    monkeypatch.setattr(bolt, "_engine", one)
    monkeypatch.setattr(bolt.voice, "settle", lambda: None)
    memory = {"gb": 100.0}
    monkeypatch.setattr(bolt.voice, "mem_available_gb", lambda: memory["gb"])
    one.memory = memory
    with agents.lock:
        agents.answering.clear()
        agents.tool_checks.clear()
    yield one
    one.hold = ""
    one.stop("test over")


def test_the_brains_address_follows_the_engine_and_the_first_question_loads_it(fake):
    assert bolt.serves("") and bolt.serves("studio:bolt") and not bolt.serves("http://127.0.0.1:8091")
    assert agents.local_server("") == (bolt.ENDPOINT, [bolt.SERVED])
    assert slate.find_local("") == (bolt.ENDPOINT, [bolt.SERVED])
    assert agents.local_answers("") and agents.automatic_default("") == "local"
    assert agents.tools_of(bolt.ENDPOINT, bolt.SERVED)  # said by the engine, nothing loaded to ask
    assert not fake.loaded() and fake.state() == "unloaded"
    reply = agents.ask("local", "", "Reply with the single word: ready", settings={**agents.saved_settings(), "local_endpoint": ""})
    assert reply == "ready" and fake.loaded() and fake.state() == "loaded"
    assert fake.port and agents.local_server("")[0] == bolt.ENDPOINT  # the address stays the engine's, whatever its port
    assert fake.events[-1]["what"] == "loaded" and fake.load_s_measured is not None
    # the brains page names it
    local = next(b for b in agents.detect(agents.saved_settings(), "studio:bolt") if b["id"] == "local")
    assert local["label"] == "NVIDIA Nemotron 3.5 Bolt (Studio engine)" and local["harness"] and local["engine"]["state"] == "loaded"


def test_an_address_typed_in_settings_stays_an_external_server(fake):
    assert not bolt.serves("http://192.168.4.194:8091")
    assert agents.local_server("http://127.0.0.1:9")[0] is None  # asked as before: nothing answers there
    assert not fake.loaded()


def test_a_release_waits_for_the_answer_in_flight_then_holds_until_the_queue_is_empty(fake):
    with bolt.request() as base:
        assert base.startswith("http://127.0.0.1:")
    said = {}

    def slow():
        with bolt.request() as live:
            said["answer"] = agents.post_json(live + "/chat/completions", {"messages": [{"role": "user", "content": "slowly"}]}, 30)
            said["done"] = time.time()

    asker = threading.Thread(target=slow)
    asker.start()
    while fake.in_flight == 0:
        time.sleep(0.01)
    assert fake.release("a video job (j1)") is True
    released = time.time()
    asker.join()
    assert said["answer"]["choices"][0]["message"]["content"] == "ready"  # the answer was never cut
    assert released >= said["done"] and not fake.loaded() and fake.state() == "held"
    with pytest.raises(bolt.Held):  # a page's question while held: refused at once, with why
        with bolt.request(wait=False):
            pass
    with pytest.raises(RuntimeError, match="unloaded while a video job"):
        agents.ask("local", "", "hello", settings={**agents.saved_settings(), "local_endpoint": ""})
    bolt.queue_empty()
    assert fake.state() == "unloaded"
    with bolt.request() as base:  # the next question loads it again
        assert fake.loaded()


def test_an_agent_turn_waits_for_its_render_with_bolt_unloaded_then_bolt_loads_again(fake):
    """The rule for the local harness: a turn's answers are requests of their own. Its clip unloads Bolt between two
    answers (while the turn waits in wait_for_jobs), the turn's next answer waits for the queue, and Bolt loads again."""
    steps = []
    with bolt.turn():
        with bolt.request() as base:
            steps.append("answer 1 (queues a clip)")
        fake.release("a video job (the turn's clip)")  # server.perform's admit: the clip does not fit beside Bolt
        steps.append("clip rendering, Bolt unloaded")
        got = {}

        def answer_two():
            with bolt.request(stopped=lambda: False, wait=True, say=lambda text: steps.append(text)) as live:
                got["loaded"] = fake.loaded()
                got["at"] = time.time()

        waiter = threading.Thread(target=answer_two)
        waiter.start()
        time.sleep(0.6)
        assert "loaded" not in got and fake.state() == "held"  # the turn waits: Bolt never loads beside the clip
        finished = time.time()
        bolt.queue_empty()  # the clip is done and the queue is empty
        waiter.join(10)
        assert got["loaded"] and got["at"] >= finished
    assert any("unloaded while a video job" in step for step in steps)


def test_a_cancelled_turn_stops_waiting(fake):
    fake.release("a video job")
    with pytest.raises(InterruptedError):
        with bolt.request(stopped=lambda: True, wait=True):
            pass


def test_no_idle_unload_while_a_turn_is_open_and_never_keeps_it(fake, monkeypatch):
    with bolt.request():
        pass
    fake.idle_s = 0.01
    with bolt.turn():
        fake.last_used = time.time() - 60
        time.sleep(5.5)  # one look of the idle thread
        assert fake.loaded()
    fake.last_used = time.time() - 60
    deadline = time.time() + 12
    while fake.loaded() and time.time() < deadline:
        time.sleep(0.2)
    assert not fake.loaded() and fake.events[-1]["why"].startswith("idle for")
    assert bolt.idle_seconds("never") == 0 and bolt.idle_seconds("0") == 0 and bolt.idle_seconds("") == 600 and bolt.idle_seconds("120") == 120


def test_the_kill_guard_ends_the_engine_under_the_floor(fake):
    with bolt.request():
        pass
    process = fake.process
    fake.memory["gb"] = 3.0  # under BOLT_GUARD_GB (8)
    process.wait(10)
    assert "memory guard killed" in fake.killed
    fake.memory["gb"] = 100.0
    deadline = time.time() + 10
    while fake.phase == "loaded" and time.time() < deadline:
        time.sleep(0.2)
    assert fake.state() == "unloaded" and "memory guard" in fake.events[-1]["why"]


def test_a_load_without_the_memory_is_refused_with_the_reason(fake):
    fake.NEED_GB = 50.0
    fake.memory["gb"] = 30.0
    with pytest.raises(RuntimeError, match="needs about 50 GB of free memory"):
        with bolt.request(wait=False):
            pass
    assert not fake.loaded()


def test_a_job_that_fits_beside_bolt_keeps_it_and_one_that_does_not_unloads_it(fake, monkeypatch):
    with bolt.request():
        pass
    assert bolt.room_mb() == int(100.0 * 1024) and server.held_room() >= bolt.room_mb()
    seen = {}

    def build(kind, params, job_id, room=True):  # a stand-in render: what it sees of Bolt
        seen[job_id] = fake.loaded()
        out = server.HOME / "renders" / f"{job_id}.png"
        return [sys.executable, "-c", f"open({str(out)!r}, 'wb').write(b'png')"], {}, [str(out)]

    monkeypatch.setattr(server, "build", build)
    job = lambda name: {"id": name, "kind": "image", "status": "running", "title": "t", "params": {}, "log": "", "outputs": [],
                        "started": time.time(), "finished": None, "remote": None}
    monkeypatch.setattr(server, "bolt_need", lambda job, resident: (True, f"{resident:.0f} GB beside it fits"))
    one = job("keeps")
    server.perform(one)
    assert one["status"] == "done" and seen["keeps"] is True and "stays loaded" in one["log"]
    monkeypatch.setattr(server, "bolt_need", lambda job, resident: (False, "the clip needs it"))
    two = job("needs")
    server.perform(two)
    assert two["status"] == "done" and seen["needs"] is False and "was unloaded to give this job its memory" in two["log"]
    assert fake.state() == "held" and bolt.room_mb() is None
    bolt.queue_empty()
    with bolt.request():
        pass
    monkeypatch.setattr(server, "bolt_need", lambda job, resident: None)  # no measured peak for the kind: it goes too
    three = job("unmeasured")
    server.perform(three)
    assert seen["unmeasured"] is False and "no measured peak" in three["log"]
    bolt.queue_empty()
    assert fake.state() == "unloaded"


def test_a_job_on_a_worker_spark_never_unloads_it(fake, monkeypatch):
    with bolt.request():
        pass
    monkeypatch.setattr(server, "bolt_need", lambda job, resident: (False, "would not fit"))
    monkeypatch.setattr(server, "take", lambda job, params, label="": (True, {}, [str(server.HOME / "x.png")]))
    (server.HOME / "x.png").parent.mkdir(parents=True, exist_ok=True)
    (server.HOME / "x.png").write_bytes(b"png")
    remote = {"id": "w1", "kind": "image", "status": "running", "title": "t", "params": {}, "log": "", "outputs": [],
              "started": time.time(), "finished": None, "remote": "http://127.0.0.1:9"}
    server.perform(remote)
    assert fake.loaded() and "Bolt" not in remote["log"]


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac's rule: vm_stat's available memory against the measured peaks")
def test_on_a_mac_the_measured_peaks_decide(fake):
    with bolt.request():
        pass
    fake.memory["gb"] = 200.0  # a 256 GB Mac with Bolt loaded
    fits, said = server.bolt_need({"kind": "video", "params": {}}, 28.0)
    assert fits and "33 GB" in said
    assert server.bolt_need({"kind": "video", "params": {"quality": "turbo"}}, 28.0)[0]
    fake.memory["gb"] = 58.0  # a 96 GB Mac
    assert server.bolt_need({"kind": "video", "params": {}}, 28.0)[0]
    assert not server.bolt_need({"kind": "voice", "params": {}}, 28.0)[0]  # Drama 3's 52 GB leaves under 8
    assert not server.bolt_need({"kind": "something-new", "params": {}}, 28.0)[0]  # unmeasured: the largest peak


def test_the_leash_ends_the_server_when_the_apps_pipe_closes():
    folder = Path(tempfile.mkdtemp())
    pid_file = folder / "pid"
    child = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(600)"
    leash = subprocess.Popen([sys.executable, "-I", str(ROOT / "app" / "bolt_leash.py"), "--", sys.executable, "-c", child],
                             stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    leash.stdin.close()
    leash.wait(10)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_the_backends_and_their_command_lines(monkeypatch, tmp_path):
    for told, kind in (("cuda", bolt.Cuda), ("metal", bolt.Metal), ("llamacpp", bolt.Llamacpp), ("external", bolt.External)):
        monkeypatch.setenv("BOLT_BACKEND", told)
        assert bolt.choose()[0] is kind
    # TensorFold's engine: Mia's settings trimmed to sit beside renders, the drafter and the libraries named for it
    monkeypatch.setenv("BOLT_BIN", str(tmp_path / "native" / "bin" / "tensorfold-native"))
    monkeypatch.setenv("BOLT_MODEL", str(tmp_path / "ckpt"))
    monkeypatch.setenv("BOLT_DRAFT_MODEL", str(tmp_path / "draft"))
    monkeypatch.setenv("BOLT_KERNEL_CACHE", str(tmp_path / "cache"))
    one = bolt.Cuda()
    argv = one.argv(8123)
    assert argv[:3] == [str(tmp_path / "native/bin/tensorfold-native"), "serve", str(tmp_path / "ckpt")]
    for pair in (("--host", "127.0.0.1"), ("--port", "8123"), ("--parallel", "4"), ("--context", "65536"), ("--prompt-cache-gib", "2"),
                 ("--name", "nvidia-nemotron-3.5-bolt")):
        assert argv[argv.index(pair[0]) + 1] == pair[1]
    assert "--no-thinking" in argv and "--vision" in argv
    env = one.environment()
    assert env["BOLT_DRAFT"] == str(tmp_path / "draft") and env["LD_LIBRARY_PATH"].split(":")[0] == str(tmp_path / "native/lib")
    assert env["TENSORFOLD_BOLT_LIB"].endswith("libbolt.so") and env["HF_HUB_OFFLINE"] == "1"
    if sys.platform.startswith("linux"):
        assert "No NVIDIA Nemotron 3.5 Bolt engine at" in one.present() and "build-bolt.sh" in one.present()
    else:
        assert one.present() == "The CUDA backend runs on a DGX Spark (Linux, CUDA)."
    # llama.cpp on a Mac until the Metal engine lands: the Mac service's flags, on a loopback port of its own
    monkeypatch.setenv("BOLT_LLAMACPP_BIN", str(tmp_path / "llama-server"))
    monkeypatch.setenv("BOLT_GGUF", str(tmp_path / "bolt.gguf"))
    lc = bolt.Llamacpp()
    argv = lc.argv(8094)
    assert argv[:3] == [str(tmp_path / "llama-server"), "-m", str(tmp_path / "bolt.gguf")]
    assert "--jinja" in argv and argv[argv.index("--host") + 1] == "127.0.0.1" and argv[argv.index("--port") + 1] == "8094"
    assert "No llama-server at" in lc.present()
    # the Metal engine is the same command line: the swap-in is a build and a setting
    monkeypatch.setenv("BOLT_BIN", str(tmp_path / "metal" / "bin" / "tensorfold-native"))
    assert bolt.Metal().argv(1)[1] == "serve"


def test_the_routes_and_the_telemetry(fake):
    said = client.get("/api/bolt/state").json()
    assert said["state"] == "unloaded" and said["backend"] == "fake" and said["licence"].startswith("NVIDIA Nemotron 3.5 Bolt")
    said = client.post("/api/bolt/load", json={}).json()
    deadline = time.time() + 10
    while client.get("/api/bolt/state").json()["state"] != "loaded" and time.time() < deadline:
        time.sleep(0.1)
    assert fake.loaded()
    reading = client.get("/api/telemetry").json()
    assert reading["engines"]["bolt"]["state"] == "loaded" and reading["engines"]["bolt"]["memory_gb"] is not None
    assert client.get("/api/state").json()["bolt"]["state"] == "loaded"
    assert client.post("/api/bolt/unload", json={}).json()["state"] == "unloaded" and not fake.loaded()
    fake.release("a video job")
    assert client.post("/api/bolt/load", json={}).status_code == 409  # held for a render: the page says why


def test_settings_take_the_engines_address():
    assert slate.nearby_endpoint("studio:bolt") == "studio:bolt"
    assert agents.save_settings({"local_endpoint": "studio:bolt"})["local_endpoint"] == "studio:bolt"
    agents.save_settings({"local_endpoint": ""})


def test_the_pages_show_the_engine():
    machine = (ROOT / "app" / "static" / "machine.js").read_text()
    assert "boltPanel(localB.engine)" in machine and '"/api/bolt/" + what' in machine and "Bolt brain" in machine
    assert '"Studio engine · Bolt", "studio:bolt"' in (ROOT / "app" / "static" / "settings.js").read_text()
    assert "Bolt brain " in (ROOT / "app" / "static" / "shell.js").read_text()
    source = (ROOT / "app" / "server.py").read_text()
    assert "bolt.admit(job" in source and "bolt.queue_empty()" in source and "bolt.room_mb()" in source
    assert json.loads(json.dumps(bolt.MAC_PEAK_GB))["video"] == 33.0
