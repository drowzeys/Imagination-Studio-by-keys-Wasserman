"""Sparks with a Mac at the head: python3 tests/test_sparks_mac.py. A Mac leads worker Sparks the way a Spark does, and
reaches a Spark it cannot route to through one that can (a worker's ``via``). No GPU and no other machine: what goes
over ssh is read as the command it is, and the relay that runs on the Spark between them is run here, with a stand-in
for its ssh."""
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import sparks  # noqa: E402

HEAD = "keyspark@192.168.4.174"
WORKER = "10.100.10.5"
# what a DGX Spark's app says it renders (server.about_this_spark): no 2x decoder presets, FastH3 qualities only
SPARK_RENDERS = {"presets": ["small", "native", "p720", "sharp", "custom"], "qualities": ["fh8", "fh4", "fh20"]}
SPARK_INFO = {"fasth3": True, "guard": True, "loras": ["camera-motion"], "ffmpeg": True, "text_encoders": ["other_minimax_h3.safetensors"],
              "memory": {"available_mb": 116000}, "disk_gb": 50, **SPARK_RENDERS}


def mac_hub(home: Path, spark: bool = False, timing=None) -> sparks.Hub:
    """A head on a Mac (or, with ``spark``, on a Spark) with one worker that is up, reached through the head Spark."""

    hub = sparks.Hub(home, ROOT, spark, {}, [], threading.Lock(), threading.Event(), lambda: {"version": "3.0.1", "disk_gb": 900},
                     lambda path: home / path, lambda job, worker: None, timing)
    hub.keep({"workers": [{"address": WORKER, "name": "gx10-5482", "folder": "keys-Mac-TensorFold-Studio", "enabled": True, "via": HEAD}], "sparks": 2})
    hub.state(WORKER).update(state="ready", info=dict(SPARK_INFO))
    return hub


def test_a_mac_leads_the_sparks():
    with tempfile.TemporaryDirectory() as scratch:
        hub = mac_hub(Path(scratch), timing=lambda kind, params: 66)
        seen = hub.overview()
        assert seen["supported"] and seen["head"] == "mac" and seen["this"]["kind"] == "Mac" and seen["available"] == 2
        assert seen["via"] == HEAD and seen["workers"][0]["via"] == HEAD and seen["this"]["memory_gb"] is None
        job = {"kind": "video", "params": {"prompt": "x", "preset": "small", "quality": "fh8", "frames": 124}, "sparks": {"chosen": 2}}
        assert hub.team(job) == [WORKER]
        said = hub.plan({"sparks": 2, "kind": "video", "clips": 8, "params": job["params"]})
        assert said["supported"] and said["used"] == 2 and said["mode"] == "parallel"
        # the measured run had a Spark at the head: from a Mac the time is an estimate that says so
        assert said["estimate"]["measured"] is False and "not a Mac" in said["estimate"]["note"]
        assert "This Mac alone." == hub.plan({"sparks": 1, "kind": "video", "params": job["params"]})["says"]
        assert "this Mac" in hub.plan({"sparks": 2, "kind": "long", "params": {}})["says"]
        # a finished clip is written down on a Mac too
        hub.record({"id": "j1", "kind": "video", "outputs": ["renders/j1.mp4"], "params": job["params"], "spark": "gx10-5482", "remote": WORKER, "sparks": {"chosen": 2}})
        assert hub.takes()["renders/j1.mp4"]["spark"] == "gx10-5482"
        # what the page says of a worker speaks of this Mac, not of a Spark at the head
        hub.state(WORKER).update(state="update", why="Its Studio code differs from this Spark's in 2 files")
        row = hub.overview()["workers"][0]
        assert row["says"] == "its Studio code differs from this Mac's" and "this Mac's in 2 files" in row["why"] and "Spark's" not in row["unusable"]


def test_a_spark_is_given_only_what_it_renders_with_its_own_settings():
    plain = {"prompt": "x", "preset": "small", "quality": "fh8"}
    assert sparks.capable("video", plain, SPARK_INFO, spark=False) == ""
    assert sparks.capable("video", {**plain, "preset": "custom", "width": 1024, "height": 576, "steps": 6}, SPARK_INFO, spark=False) == ""
    assert sparks.capable("video", {**plain, "loras": [{"id": "camera-motion"}]}, SPARK_INFO, spark=False) == ""
    # the Mac's own: MiniMax H3 Turbo's qualities, the presets made with the 2x decoder, the Mac's text encoder files
    for quality in ("standard", "high", "full", "fast"):
        assert "MiniMax H3 Turbo" in sparks.capable("video", {**plain, "quality": quality}, SPARK_INFO, spark=False)
    for preset, name in (("draft", "Draft"), ("2k", "2K"), ("qhd", "2K 2560x1440"), ("p960", "960p")):
        assert f"the {name} preset is made with the 2x decoder" in sparks.capable("video", {**plain, "preset": preset}, SPARK_INFO, spark=False)
    assert "2x decoder" in sparks.capable("video", {"prompt": "x", "quality": "fh8"}, SPARK_INFO, spark=False)  # no preset: a Mac's is Draft
    said = sparks.capable("video", {**plain, "text_encoder": "/Users/me/h3-models/text_encoders/other_minimax_h3.safetensors"}, SPARK_INFO, spark=False)
    assert "Mac" in said and "ComfyUI" in said
    # a reference clip runs on Ref2VA, where the quality does not apply; it needs Ref2VA there as before
    assert "Ref2VA" in sparks.capable("video", {**plain, "quality": "standard", "references": ["uploads/a.png"]}, SPARK_INFO, spark=False)
    assert sparks.capable("video", {**plain, "quality": "standard", "references": ["uploads/a.png"]}, {**SPARK_INFO, "ref2va": True}, spark=False) == ""
    # a Spark at the head is held to what it was: its text encoder files go by name, no preset means small
    assert sparks.capable("video", {"prompt": "x", "quality": "fh8", "text_encoder": "other_minimax_h3.safetensors"}, SPARK_INFO) == ""
    # a worker from before this says nothing of its presets: nothing is refused for them
    assert sparks.capable("video", {**plain, "preset": "draft"}, {"fasth3": True}, spark=False) == ""
    # and the plan says why each Spark was left out
    with tempfile.TemporaryDirectory() as scratch:
        hub = mac_hub(Path(scratch))
        said = hub.plan({"sparks": 2, "kind": "video", "clips": 4, "params": {**plain, "quality": "standard"}})
        assert said["used"] == 1 and "MiniMax H3 Turbo" in said["without"][0]["why"]
        assert hub.team({"kind": "video", "params": {**plain, "preset": "qhd"}, "sparks": {"chosen": 2}}) == []


def test_through_a_spark_each_command_is_sent_once_quoted_and_runs_on_its_key():
    command = 'F="$HOME/keys"; echo "host $(hostname)"; (exec 3<>/dev/tcp/127.0.0.1/7870) 2>/dev/null && echo "app yes"; true'
    argv = sparks.hop(HEAD, WORKER, command)
    assert argv[:len(sparks.SSH)] == sparks.SSH and argv[len(sparks.SSH)] == HEAD and len(argv) == len(sparks.SSH) + 2
    # what the Spark's shell reads back is ssh, with the same options, to the worker, and the command as one word
    assert shlex.split(argv[-1]) == [*sparks.SSH, WORKER, command]
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "ok", ""

    had = sparks.subprocess.run
    sparks.subprocess.run = lambda argv, **kwargs: (seen.append((argv, kwargs)), Done())[1]
    try:
        assert sparks.shell(WORKER, "true", via=HEAD) == (0, "ok") and seen[-1][0] == sparks.hop(HEAD, WORKER, "true")
        assert sparks.shell(WORKER, "true")[0] == 0 and seen[-1][0] == [*sparks.SSH, WORKER, "true"]  # without via: as before
        sparks.copy(ROOT, WORKER, "keys-Mac-TensorFold-Studio", via=HEAD)
        rsync = seen[-1][0]
        # the copy goes through the Spark in rsync's -e, words without spaces in them, which macOS's openrsync splits alike
        assert rsync[0] == "rsync" and rsync[rsync.index("-e") + 1] == " ".join([*sparks.SSH, HEAD, *sparks.SSH])
        assert rsync[-1] == f"{WORKER}:keys-Mac-TensorFold-Studio/" and ".DS_Store" in rsync and all(" " not in word for word in sparks.SSH)
        sparks.copy(ROOT, WORKER, "keys-Mac-TensorFold-Studio")
        assert seen[-1][0][seen[-1][0].index("-e") + 1] == " ".join(sparks.SSH)
        assert all("timeout" in kwargs for _, kwargs in seen)
    finally:
        sparks.subprocess.run = had
    # an address that is not one is refused before anything runs, for via as for the worker
    for bad in ("-oProxyCommand=x", "a b", "host;rm"):
        try:
            sparks.address_of(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


FAKE_SSH = r"""#!/usr/bin/env python3
# the Spark's ssh to the worker, stood in for: it binds the socket it is asked to forward and waits, as ssh -N does
import socket, sys, time
spec = sys.argv[sys.argv.index("-L") + 1]
path = spec.rsplit(":127.0.0.1:", 1)[0]
open(sys.argv[0] + ".args", "w").write(" ".join(sys.argv[1:]))
s = socket.socket(socket.AF_UNIX); s.bind(path); s.listen(1)
time.sleep(300)
"""


def relay_there(scratch: Path, beat: float):
    """The relay as the Spark runs it, here: (process, socket path, the stand-in ssh's file)."""

    tools = scratch / "bin"
    tools.mkdir()
    (tools / "ssh").write_text(FAKE_SSH.replace("#!/usr/bin/env python3", "#!" + sys.executable))
    (tools / "ssh").chmod(0o755)
    place = scratch / "r.sock"
    had = sparks.BEAT
    sparks.BEAT = beat
    try:
        script = sparks.Relay(WORKER, HEAD).script(str(place), 5)
    finally:
        sparks.BEAT = had
    process = subprocess.Popen(["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                               env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}, start_new_session=True)
    return process, place, tools / "ssh.args"


def gone(process, place: Path, limit: float = 5) -> bool:
    until = time.time() + limit
    while time.time() < until:
        if process.poll() is not None and not place.exists():
            return True
        time.sleep(0.05)
    return False


def test_the_relay_on_the_spark_ends_with_the_macs_ssh_and_leaves_nothing():
    with tempfile.TemporaryDirectory() as folder:
        scratch = Path(folder)
        process, place, said = relay_there(scratch, 10)
        try:
            assert process.stdout.readline().strip() == "ready" and place.is_socket()
            # the Spark's own ssh to the worker: key login only, the socket to the worker's own 127.0.0.1:7870
            args = said.read_text()
            assert "BatchMode=yes" in args and f"-L {place}:127.0.0.1:7870 {WORKER}" in args and "0.0.0.0" not in args
            process.stdin.write("\n")
            process.stdin.flush()
            time.sleep(0.3)
            assert process.poll() is None  # beating: it stays
            process.stdin.close()  # the Mac's ssh is gone (closed, killed, or this app stopped)
            assert gone(process, place), "the relay outlived the ssh that started it"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
        # no stand-in ssh is left running
        left = subprocess.run(["pgrep", "-f", str(scratch / "bin" / "ssh")], capture_output=True, text=True, timeout=5)
        assert left.stdout.strip() == ""
    # a Mac that goes silent without closing (its network gone): three missed heartbeats end it as well
    with tempfile.TemporaryDirectory() as folder:
        process, place, _ = relay_there(Path(folder), 1)
        try:
            assert process.stdout.readline().strip() == "ready"
            assert gone(process, place, 8), "the relay did not end without its heartbeat"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.stdin.close()


def test_the_relay_is_one_ssh_from_here_and_closes_with_this_app():
    seen = {}

    class Pipe:
        def __init__(self, lines=()):
            self.lines, self.closed = list(lines), False

        def readline(self):
            return self.lines.pop(0) if self.lines else ""

        def read(self):
            return ""

        def write(self, text):
            if self.closed:
                raise ValueError("closed")

        def flush(self):
            pass

        def close(self):
            self.closed = True

        def fileno(self):
            return seen["r"]

    class Process:
        pid = 0

        def __init__(self, command, **kwargs):
            seen["command"], seen["live"] = command, True
            self.stdin, self.stdout, self.stderr = Pipe(), Pipe(["ready\n"]), Pipe()

        def poll(self):
            return None if seen["live"] and not self.stdin.closed else 0

        def terminate(self):
            seen["live"] = False

        def wait(self, timeout=None):
            return 0

    seen["r"], writer = os.pipe()
    os.write(writer, b"ready\n")
    had = sparks.subprocess.Popen, sparks.socket.create_connection, sparks.shell
    sparks.subprocess.Popen = Process
    sparks.socket.create_connection = lambda *args, **kwargs: type("S", (), {"close": lambda self: None})()
    sparks.shell = lambda address, command, timeout=30, via=None: (seen.setdefault("asked", []).append((address, command)), (0, "/home/keyspark/.cache/studio-sparks\n"))[1]
    try:
        relay = sparks.Relay(WORKER, HEAD)
        base = relay.open()
        command = seen["command"]
        assert seen["asked"][0][0] == HEAD and "chmod 700" in seen["asked"][0][1]  # a folder of the user's own there
        assert command[:len(sparks.SSH)] == sparks.SSH and command[-2] == HEAD and "-N" not in command
        forward = command[command.index("-L") + 1]
        assert forward.startswith(f"127.0.0.1:{relay.port}:/home/keyspark/.cache/studio-sparks/") and forward.endswith(".sock")
        assert base == f"http://127.0.0.1:{relay.port}" and relay.mark == forward
        there = shlex.split(command[-1])
        assert there[:2] == ["bash", "-c"] and f'-L "$S:127.0.0.1:7870" {WORKER}' in there[2] and "0.0.0.0" not in there[2]
        assert relay.alive()
        relay.close()
        assert not relay.alive()
    finally:
        sparks.subprocess.Popen, sparks.socket.create_connection, sparks.shell = had
        os.close(seen["r"])
        os.close(writer)
    # the relay's script is one the Spark's bash reads
    assert subprocess.run(["bash", "-n", "-c", there[2]], capture_output=True, timeout=10).returncode == 0
    # the head reaches its worker through the relay when, and only when, the worker has a via
    with tempfile.TemporaryDirectory() as scratch:
        hub = mac_hub(Path(scratch))
        assert isinstance(hub.dial(WORKER), sparks.Relay) and hub.dial(WORKER).via == HEAD
        assert type(hub.dial("10.100.10.1")) is sparks.Tunnel


def test_a_tunnel_left_by_a_killed_run_is_closed_on_a_mac_too():
    """A run that was killed left its ssh: the next start ends it, read with ps where there is no /proc (macOS)."""

    mark = "127.0.0.1:59999:/home/keyspark/.cache/studio-sparks/x.sock"
    # a process whose command is ssh with that forward (sh as ssh, waiting)
    ours = subprocess.Popen(["ssh", "-c", "sleep 30; :", "-L", mark, HEAD], executable="/bin/sh")
    other = subprocess.Popen(["ssh", "-c", "sleep 30; :", "-L", "127.0.0.1:1:/elsewhere.sock", HEAD], executable="/bin/sh")
    try:
        time.sleep(0.2)
        assert sparks.command_of(ours.pid)[:1] == ["ssh"] and mark in sparks.command_of(ours.pid)
        with tempfile.TemporaryDirectory() as scratch:
            hub = mac_hub(Path(scratch))
            (hub.folder / "tunnels.json").write_text(json.dumps([[ours.pid, mark], [other.pid, mark]]))
            hub.sweep()
            assert ours.wait(timeout=5) is not None and other.poll() is None  # only the process that is that tunnel
    finally:
        for one in (ours, other):
            if one.poll() is None:
                one.kill()
            one.wait()


def test_a_worker_is_added_with_its_via_and_checked_through_it():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as scratch:
        hub = mac_hub(Path(scratch))
        hub.keep({"workers": [], "sparks": 1})
        asked = []
        hub.shell = lambda address, command, timeout=30, via=None: (asked.append((address, via)), (255, "Host key verification failed."))[1]
        app = FastAPI()
        app.include_router(hub.router())
        client = TestClient(app)
        assert client.post("/api/sparks/workers", json={"address": WORKER, "via": "-oProxyCommand=x"}).status_code == 400
        seen = client.post("/api/sparks/workers", json={"address": WORKER, "folder": "keys-Mac-TensorFold-Studio", "via": HEAD}).json()
        assert seen["workers"][0]["via"] == HEAD
        until = time.time() + 5
        while time.time() < until and client.get("/api/sparks").json()["workers"][0]["state"] == "new":
            time.sleep(0.05)
        row = client.get("/api/sparks").json()["workers"][0]
        assert asked and asked[0] == (WORKER, HEAD)
        assert row["state"] == "unreachable" and f"through {HEAD}" in row["why"] and "Host key verification failed" in row["why"]
        # the way to it changed (or cleared): the next look goes the new way
        assert client.post(f"/api/sparks/workers/{WORKER}", json={"via": ""}).json()["workers"][0]["via"] is None
        assert hub.via(WORKER) is None


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
