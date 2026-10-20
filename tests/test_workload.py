"""The workload governor path (spark/workload.py, managed_comfy.py, managed_voice.py, start_gate.py), off by default,
and the brain defaults, Drama 3's managed guard and the delivery sound that came with it. The governor here is a fake
written into a temporary folder: three small modules with the real one's names and calls."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "spark"))
sys.path.insert(0, str(ROOT / "app"))
import workload as W  # noqa: E402

GIB = 1024 ** 3

FAKE = {
    "admission_client.py": '''
import json, os
from pathlib import Path
LOG = Path(__file__).with_name("calls.jsonl")
def rpc(request, socket_path=None):
    with LOG.open("a") as out:
        out.write(json.dumps(request) + "\\n")
    return {"allowed": True, "reservations": []}
''',
    "process_binding.py": '''
import os
def process(pid):
    if pid is None:
        return None
    try:
        os.kill(int(pid), 0)
    except OSError:
        return None
    return {"pid": int(pid), "uid": os.getuid(), "state": "S", "sid": int(pid), "start": "7"}
def session(binding):
    return [binding["pid"]] if process(binding["pid"]) else []
''',
    "governor.py": '''
import json
from pathlib import Path
GIB = 1024 ** 3
def collect_snapshot():
    return json.loads(Path(__file__).with_name("snapshot.json").read_text())
def sensor_errors(snapshot):
    return snapshot.get("sensor_errors", [])
''',
}


def snapshot(free_gib=60.0, gpu_c=50.0, some=0.0, full=0.0, flags=None):
    return {"memory": {"available_bytes": int(free_gib * GIB)},
            "gpus": [{"temperature_c": gpu_c, "thermal_flags": flags or {"slowdown": False}}],
            "psi": {"some_avg10": some, "full_avg10": full}}


ZONES = [{"zone": f"thermal_zone{i}", "temp_c": 45.0, "ceiling_c": 90.0} for i in range(7)]


@pytest.fixture
def governed(monkeypatch):
    where = Path(tempfile.mkdtemp())
    (where / "state").mkdir()
    for name, text in FAKE.items():
        (where / name).write_text(text)
    (where / "snapshot.json").write_text(json.dumps(snapshot()))
    (where / "state/reservations.json").write_text(json.dumps({"reservations": []}))
    prefix = Path(tempfile.mkdtemp())
    for name in ("admission_client", "process_binding", "governor"):
        sys.modules.pop(name, None)
    monkeypatch.setenv("SPARK_GOVERNOR", str(where))
    monkeypatch.setenv("SPARK_COMFY_PORT", "8199")
    monkeypatch.delenv("SPARK_ADMISSION_TOKEN", raising=False)
    monkeypatch.delenv("SPARK_GOVERNOR_OWNER", raising=False)
    monkeypatch.setattr(W, "PREFIX", prefix)
    monkeypatch.setattr(W, "zones", lambda: [dict(z) for z in ZONES])
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    if str(where) in sys.path:
        sys.path.remove(str(where))
    yield where
    for name in ("admission_client", "process_binding", "governor"):
        sys.modules.pop(name, None)
    shutil.rmtree(where, ignore_errors=True)
    shutil.rmtree(prefix, ignore_errors=True)


def reserve(where: Path, owner: str, pid: int, token: str = "t1") -> None:
    rows = json.loads((where / "state/reservations.json").read_text())["reservations"]
    rows.append({"owner": owner, "token": token, "gib": 85, "binding": {"pid": pid, "start": "7"}})
    (where / "state/reservations.json").write_text(json.dumps({"reservations": rows}))


def calls(where: Path) -> list[dict]:
    log = where / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


# ---------- off by default ----------

def test_without_a_governor_nothing_changes(monkeypatch):
    monkeypatch.delenv("SPARK_GOVERNOR", raising=False)
    assert not W.enabled()
    done = subprocess.run([sys.executable, str(ROOT / "spark/start_gate.py")], capture_output=True, text=True,
                          env={k: v for k, v in os.environ.items() if k != "SPARK_GOVERNOR"})
    assert done.returncode == 0 and not done.stdout and not done.stderr
    script = (ROOT / "spark/comfy.sh").read_text()
    # the governor's branch is taken only when it is named; the upstream start and stop stay as they were
    assert script.count('if [ -n "${SPARK_GOVERNOR:-}" ]') == 2
    assert 'nice -n 19 "$PREFIX/venv/bin/python" main.py --listen 127.0.0.1 --port "$PORT"' in script
    generate = (ROOT / "spark/spark_generate.py").read_text()
    assert 'if os.environ.get("SPARK_GOVERNOR"):' in generate
    assert "    gate()\n    import aiohttp" in generate
    assert "    sg.gate()\n    import aiohttp" in (ROOT / "spark/spark_workflows.py").read_text()


def test_a_governed_stop_kills_only_the_server_process_of_its_port():
    """The wrapper's own command line names the port too: after the managed stop, only `<python> main.py ...` on
    this port is killed, so the wrapper ends its reservation itself."""

    import re

    script = (ROOT / "spark/comfy.sh").read_text()
    pattern = 'MATCH="^[^ ]+ main\\.py --listen 127\\.0\\.0\\.1 --port $PORT( |\\$)"'
    assert pattern in script
    regex = r"^[^ ]+ main\.py --listen 127\.0\.0\.1 --port 8292( |$)"
    assert re.search(regex, "/opt/venv/bin/python main.py --listen 127.0.0.1 --port 8292 --use-ck-attention")
    assert not re.search(regex, "python3 /x/spark/managed_comfy.py run /opt/venv/bin/python main.py --listen 127.0.0.1 --port 8292")
    assert not re.search(regex, "/opt/venv/bin/python main.py --listen 127.0.0.1 --port 82920")
    assert not re.search(regex, "/opt/venv/bin/python main.py --listen 127.0.0.1 --port 8291")


# ---------- limits ----------

def test_the_limits_never_go_under_the_floor(monkeypatch):
    monkeypatch.setenv("SPARK_GOVERNOR_MIN_FREE_GIB", "4")
    assert W.min_free_gib() == 12
    monkeypatch.setenv("SPARK_GOVERNOR_MIN_FREE_GIB", "20")
    assert W.min_free_gib() == 20


def test_what_stops_a_server():
    assert W.reasons(snapshot(), ZONES, 7, 0, GIB) == []
    assert "free memory below 12 GiB" in W.reasons(snapshot(free_gib=11.5), ZONES, 7, 0, GIB)
    assert W.reasons(snapshot(gpu_c=90), ZONES, 7, 0, GIB) == ["GPU temperature ceiling or thermal slowdown"]
    assert W.reasons(snapshot(flags={"slowdown": True}), ZONES, 7, 0, GIB) == ["GPU temperature ceiling or thermal slowdown"]
    assert W.reasons(snapshot(), ZONES[:6], 7, 0, GIB) == ["platform temperature ceiling or unavailable zone"]
    assert W.reasons(snapshot(), [], 0, 0, GIB) == ["platform temperature ceiling or unavailable zone"]
    hot = [dict(z) for z in ZONES]
    hot[3]["temp_c"] = 91
    assert W.reasons(snapshot(), hot, 7, 0, GIB) == ["platform temperature ceiling or unavailable zone"]
    assert W.reasons(snapshot(some=30), ZONES, 7, 61, GIB) == ["sustained memory pressure"]
    assert W.reasons({"memory": None}, ZONES, 7, 0, GIB) == ["required telemetry missing"]


def test_a_start_wants_a_cool_and_calm_machine():
    assert W.start_reasons(snapshot(), ZONES, 7, GIB) == []
    assert any("below 80 C" in r for r in W.start_reasons(snapshot(gpu_c=80), ZONES, 7, GIB))
    assert any("pressure" in r for r in W.start_reasons(snapshot(some=10), ZONES, 7, GIB))
    assert any("pressure" in r for r in W.start_reasons(snapshot(full=2), ZONES, 7, GIB))


def test_the_thermal_zone_count_is_the_one_first_seen_unless_named(monkeypatch):
    monkeypatch.delenv("SPARK_THERMAL_ZONES", raising=False)
    assert W.expected_zones(7) == 7
    monkeypatch.setenv("SPARK_THERMAL_ZONES", "9")
    assert W.expected_zones(7) == 9


# ---------- ownership and the gate ----------

def test_each_port_has_its_own_server_file_and_owner(governed, monkeypatch):
    assert W.pidfile("comfy").name == "managed-comfy-8199.json"
    assert W.owner() == "tensorfold-studio-comfy-8199"
    monkeypatch.setenv("SPARK_COMFY_PORT", "8200")
    assert W.pidfile("comfy").name == "managed-comfy-8200.json" and W.owner() == "tensorfold-studio-comfy-8200"
    monkeypatch.setenv("SPARK_GOVERNOR_OWNER", "studio-a")
    assert W.owner() == "studio-a"


def test_the_gate_wants_the_servers_own_live_reservation(governed):
    with pytest.raises(RuntimeError, match="no live reservation"):
        W.gate("comfy", tries=2, wait_s=0)
    W.write_identity("comfy", os.getpid())
    with pytest.raises(RuntimeError, match="no live reservation"):
        W.gate("comfy", tries=2, wait_s=0)  # a file, but no reservation bound to it
    reserve(governed, "someone-else", os.getpid())
    with pytest.raises(RuntimeError, match="no live reservation"):
        W.gate("comfy", tries=2, wait_s=0)
    reserve(governed, W.owner(), os.getpid(), token="t2")
    W.gate("comfy", tries=2, wait_s=0)
    logged = (W.PREFIX / "managed-logs/start-gates-comfy.jsonl").read_text().splitlines()
    assert json.loads(logged[-1])["reasons"] == []


def test_the_gate_waits_then_refuses_a_hot_or_pressed_machine(governed):
    W.write_identity("comfy", os.getpid())
    reserve(governed, W.owner(), os.getpid())
    (governed / "snapshot.json").write_text(json.dumps(snapshot(gpu_c=85)))
    with pytest.raises(RuntimeError, match="did not pass"):
        W.gate("comfy", tries=3, wait_s=0)
    (governed / "snapshot.json").write_text(json.dumps({**snapshot(), "sensor_errors": ["nvidia-smi timed out"]}))
    with pytest.raises(RuntimeError, match="telemetry unavailable"):
        W.gate("comfy", tries=3, wait_s=0)


def test_a_server_file_of_another_process_is_not_ours(governed):
    W.pidfile("comfy").write_text(json.dumps({"pid": os.getpid(), "start": "some other start", "uid": os.getuid()}))
    reserve(governed, W.owner(), os.getpid())
    assert W.live_identity("comfy") is None and not W.owned("comfy")


def test_only_our_ended_reservations_are_released(governed):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    reserve(governed, W.owner(), os.getpid(), token="alive")
    reserve(governed, W.owner(), dead.pid, token="ended")
    reserve(governed, "someone-else", dead.pid, token="theirs")
    W.reconcile_dead(W.owner())
    released = [c for c in calls(governed) if c["op"] == "release"]
    assert released == [{"op": "release", "owner": W.owner(), "token": "ended"}]


def test_the_admitted_command_keeps_the_floor_and_the_socket(governed, monkeypatch):
    monkeypatch.setenv("SPARK_GOVERNOR_MIN_FREE_GIB", "2")
    command = W.client_command(W.owner(), "h3", 85, ["python3", "managed_comfy.py", "run", "x"])
    assert command[command.index("--min-free-gib") + 1] == "12"
    assert command[command.index("--socket") + 1] == str(governed / "state/admission.sock")
    assert command[command.index("--residency") + 1] == "new"
    assert command[command.index("--run") + 1:] == ["python3", "managed_comfy.py", "run", "x"]


def test_the_managed_server_asks_before_it_starts(governed, monkeypatch):
    import managed_comfy

    launched = []
    monkeypatch.setattr(managed_comfy.subprocess, "call", lambda argv: launched.append(argv) or 0)
    assert managed_comfy.main(["run", "/venv/python", "main.py", "--listen", "127.0.0.1", "--port", "8199"]) == 0
    preflight = [c for c in calls(governed) if c["op"] == "preflight"]
    assert preflight and preflight[0]["owner"] == W.owner() and preflight[0]["min_free_gib"] == 12
    assert launched and launched[0][launched[0].index("--run") + 1:][-6:] == ["/venv/python", "main.py", "--listen", "127.0.0.1", "--port", "8199"]
    assert managed_comfy.main([]) == 2


def test_stopping_with_no_managed_server_is_quiet(governed, capsys):
    import managed_comfy

    assert managed_comfy.main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out


def test_another_comfyui_is_freed_only_when_idle(governed, monkeypatch):
    import io

    asked = []

    def answer(request, timeout=10):
        asked.append(request.full_url)
        body = json.dumps(state) if request.full_url.endswith("/queue") else ""
        return io.BytesIO(body.encode())

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(W.urllib.request, "urlopen", lambda r, timeout=10: Reply(answer(r).read()))
    monkeypatch.delenv("SPARK_GOVERNOR_FREE_URL", raising=False)
    W.free_other_comfy()
    assert asked == []
    monkeypatch.setenv("SPARK_GOVERNOR_FREE_URL", "http://127.0.0.1:9999")
    state = {"queue_running": [["job"]], "queue_pending": []}
    with pytest.raises(RuntimeError, match="busy"):
        W.free_other_comfy()
    assert asked == ["http://127.0.0.1:9999/queue"]
    state = {"queue_running": [], "queue_pending": []}
    W.free_other_comfy()
    assert asked[-1] == "http://127.0.0.1:9999/free"


def test_a_stop_by_the_limits_is_said_plainly():
    import spark_generate as SG

    home = Path(tempfile.mkdtemp())
    had = SG.PREFIX
    SG.PREFIX = home
    try:
        (home / "comfy.log").write_text("loading\n[studio] stopping the managed server: cool start: memory pressure is high\n")
        said = SG.stopped()
    finally:
        SG.PREFIX = had
    assert said.startswith("the render was stopped by this machine's workload limits: cool start: memory pressure is high")


# ---------- Drama 3 under the governor ----------

def test_drama3_goes_through_the_managed_launcher_only_when_asked(monkeypatch):
    import voice

    monkeypatch.delenv("DRAMA3_MANAGED_GUARD", raising=False)
    engine = voice.Cuda()
    assert engine.argv()[0] == str(engine.bin)
    monkeypatch.setenv("DRAMA3_MANAGED_GUARD", "1")
    argv = engine.argv()
    assert argv[:2] == ["python3", str(ROOT / "spark/managed_voice.py")] and argv[2] == str(engine.bin)
    assert argv[-2:] == ["--serve", "1"]


def test_a_managed_drama3_without_a_governor_is_refused(monkeypatch):
    import voice

    monkeypatch.setenv("DRAMA3_MANAGED_GUARD", "1")
    monkeypatch.delenv("SPARK_GOVERNOR", raising=False)
    monkeypatch.setattr(voice.sys, "platform", "linux")
    assert "set SPARK_GOVERNOR" in voice.Cuda().look()


def test_every_part_of_a_managed_take_is_checked_first(monkeypatch):
    import voice

    class Done:
        returncode, stdout, stderr = 1, "", "Drama 3 was not started: cool start: the GPU must be below 80 C"

    monkeypatch.setattr(voice.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(RuntimeError, match="the voice was not sent: .*below 80 C"):
        voice.submission_check()
    source = (ROOT / "app/voice.py").read_text()
    assert "                    if managed():\n                        submission_check()\n" in source


def test_managed_voice_keeps_its_own_kill_guard():
    source = (ROOT / "spark/managed_voice.py").read_text()
    assert 'float(os.environ.get("DRAMA3_GUARD_GB", 8))' in source and "engine.kill()" in source
    assert "the voice waits for it" in source  # a rendering studio is never stopped for a take


def test_the_heap_goes_back_after_drama3_is_released(monkeypatch):
    import voice

    trimmed = []

    class Fake:
        def release(self, why):
            return True

    class QA:
        @staticmethod
        def trim_heap():
            trimmed.append(1)

    monkeypatch.setattr(voice, "_backend", Fake())
    monkeypatch.setattr(voice, "qa", QA)
    assert voice.release("a render") is True and trimmed == [1]
    monkeypatch.setattr(voice, "qa", None)
    assert voice.release("a render") is True  # before attach: nothing to trim with, no error


def test_trim_heap_is_safe_everywhere():
    import qa

    qa.trim_heap()


def test_a_live_take_asked_for_early_gets_a_short_grace():
    import voice
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    assert 0 < voice.LIVE_GRACE_S <= 5
    app = FastAPI()
    app.include_router(voice.router)
    had = voice.HOME
    voice.HOME = Path(tempfile.mkdtemp())
    try:
        began = __import__("time").time()
        reply = TestClient(app).get("/api/voice/takes/unknown123/live")
        waited = __import__("time").time() - began
    finally:
        voice.HOME = had
    assert reply.status_code == 404 and voice.LIVE_GRACE_S - 0.5 <= waited < voice.LIVE_GRACE_S + 3


# ---------- the brain ----------

@pytest.fixture
def agents_home(monkeypatch):
    import agents

    home = Path(tempfile.mkdtemp())
    monkeypatch.setattr(agents, "HOME", home)
    for name in ("STUDIO_DEFAULT_BRAIN", "STUDIO_LOCAL_ENDPOINT", "STUDIO_LOCAL_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agents, "local_answers", lambda endpoint: False)  # a server on this machine never picks the default here
    yield agents
    shutil.rmtree(home, ignore_errors=True)


def test_the_environment_fills_only_what_was_never_saved(agents_home, monkeypatch):
    agents = agents_home
    assert agents.default_brain() == {"backend": "claude", "endpoint": "", "model": ""}
    monkeypatch.setenv("STUDIO_DEFAULT_BRAIN", "local")
    monkeypatch.setenv("STUDIO_LOCAL_ENDPOINT", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("STUDIO_LOCAL_MODEL", "example-vision-model")
    assert agents.default_brain() == {"backend": "local", "endpoint": "http://127.0.0.1:11434/v1", "model": "example-vision-model"}
    agents.write_json(agents.folder() / "settings.json", {"default": "codex", "local_endpoint": ""})
    # saved wins, an empty saved endpoint included; the model was never saved, so the environment's stands
    assert agents.default_brain() == {"backend": "codex", "endpoint": "", "model": "example-vision-model"}
    monkeypatch.setenv("STUDIO_DEFAULT_BRAIN", "not-a-brain")
    agents.write_json(agents.folder() / "settings.json", {})
    assert agents.default_brain()["backend"] == "claude"


def test_a_new_film_follows_the_default_brain(agents_home, monkeypatch):
    import slate

    film = slate.blank("A test film")
    assert (film["defaults"]["brain"], film["defaults"]["localEndpoint"], film["defaults"]["localModel"]) == ("auto", "", "")
    monkeypatch.setenv("STUDIO_DEFAULT_BRAIN", "local")
    monkeypatch.setenv("STUDIO_LOCAL_ENDPOINT", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("STUDIO_LOCAL_MODEL", "example-vision-model")
    # asked when the film asks, not when it was made: the Studio's brain of the moment, with its server and model
    assert slate.resolve_brain("auto") == ("local", "http://127.0.0.1:8080/v1", "example-vision-model")
    assert slate.resolve_brain("") == slate.resolve_brain("not-a-brain") == slate.resolve_brain("auto")
    # a film that names a brain keeps it
    assert slate.resolve_brain("codex", "x", "y") == ("codex", "x", "y")
    monkeypatch.delenv("STUDIO_DEFAULT_BRAIN")
    assert slate.resolve_brain("auto")[0] == "claude"
    asked = []
    monkeypatch.setattr(agents_home, "ask", lambda *a: asked.append(a) or "ok")
    assert slate.ask("auto", "sys", "hi", "top") == "ok" and asked[-1][0] == "claude"


def test_a_local_brain_never_sends_frames_to_a_cloud_cli(agents_home, monkeypatch):
    import qa

    picture = Path(tempfile.mkdtemp()) / "sheet.jpg"
    picture.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(qa, "tool", lambda name: pytest.fail(f"{name} was asked to look at a local film's frames"))
    seen = []
    monkeypatch.setattr(qa, "local_vision", lambda p, endpoint, model: seen.append((endpoint, model)) or '{"pass": true}')
    monkeypatch.setenv("STUDIO_DEFAULT_BRAIN", "local")
    monkeypatch.setenv("STUDIO_LOCAL_ENDPOINT", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("STUDIO_LOCAL_MODEL", "example-vision-model")
    # the film says local but carries no endpoint: the default one is used, and nothing else is tried
    assert qa.look(picture, {"backend": "local", "endpoint": "", "model": ""})[1] == "local model"
    assert seen == [("http://127.0.0.1:8080/v1", "example-vision-model")]
    # with no local endpoint anywhere, the look is skipped rather than sent to a cloud CLI
    monkeypatch.delenv("STUDIO_LOCAL_ENDPOINT")
    assert qa.identity_gate(picture, {"backend": "local"})["result"] == "skipped"
    # a film with no brain of its own takes the default one
    monkeypatch.setenv("STUDIO_LOCAL_ENDPOINT", "http://127.0.0.1:8080/v1")
    assert qa.look(picture, {})[1] == "local model"


def test_a_cloud_brain_still_falls_back_as_before(agents_home, monkeypatch):
    import qa

    picture = Path(tempfile.mkdtemp()) / "sheet.jpg"
    picture.write_bytes(b"\xff\xd8\xff")
    asked = []
    monkeypatch.setattr(qa, "tool", lambda name: asked.append(name) or None)
    with pytest.raises(LookupError):
        qa.look(picture, {"backend": "claude"})
    assert asked == ["claude", "codex"]


# ---------- the delivery sound ----------

def delivery_block() -> str:
    script = (ROOT / "spark/video.sh").read_text()
    start = script.index('if [ -n "${DELIVERY_AUDIO_RATE:-}" ]')
    end = script.index("\nfi\n", start) + 4
    return script[start:end]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")
def test_the_delivery_sound_is_resampled_only_when_asked():
    folder = Path(tempfile.mkdtemp())
    clip = folder / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=24:duration=1", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=32000:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "1", "-shortest", str(clip)], check=True)

    def probe(stream: str) -> str:
        entries = "stream=sample_rate,channels" if stream.startswith("a") else "stream=codec_name,width,height,nb_frames"
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries,
                               "-of", "csv=p=0", str(clip)],
                              capture_output=True, text=True, check=True).stdout.strip()

    picture = probe("v:0")
    block = "set -euo pipefail\n" + delivery_block()
    run = lambda env: subprocess.run(["bash", "-c", block], env={**os.environ, "OUT": str(clip), **env},  # noqa: E731
                                     capture_output=True, text=True)
    unset = {k: v for k, v in os.environ.items() if k != "DELIVERY_AUDIO_RATE"}
    assert subprocess.run(["bash", "-c", block], env={**unset, "OUT": str(clip)}).returncode == 0
    assert probe("a:0") == "32000,1"
    done = run({"DELIVERY_AUDIO_RATE": "48000"})
    assert done.returncode == 0, done.stderr
    assert probe("a:0") == "48000,2" and "stereo 48 kHz" in done.stdout
    assert probe("v:0") == picture  # the picture copied, not encoded again
    assert not list(folder.glob("clip.audio-*"))
    assert run({"DELIVERY_AUDIO_RATE": "12345"}).returncode == 2
