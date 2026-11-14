"""A Mac as a worker: python3 tests/test_sparks_mac_worker.py. A head (a Spark or a Mac) hands a Mac single clips the
way it hands them to a Spark. No GPU and no other machine: what goes over ssh is read as the command it is, and the
commands that start, find and stop a worker's app are run here, in bash and (where it is installed) in zsh, a Mac's
login shell, against a stand-in app."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import sparks  # noqa: E402

MAC = "you@192.168.1.20"
# what a Mac's app says it renders (server.about_this_spark): every preset, FastH3's qualities and, with its weights, Turbo's
MAC_INFO = {"platform": "mac", "fasth3": True, "guard": True, "loras": ["camera-motion"], "ffmpeg": True, "text_encoders": ["other_h3_mlx.safetensors"],
            "memory": {"available_mb": 190000, "total_mb": 262144, "floor_mb": 26214}, "disk_gb": 400, "code": {},
            "presets": ["draft", "2k", "qhd", "small", "native", "p720", "p960", "sharp", "custom"], "qualities": ["standard", "high", "full", "fast", "fh8", "fh4", "fh20"]}
PLAIN = {"prompt": "x", "preset": "small", "quality": "fh8", "frames": 124}


def hub_with_a_mac(home: Path, spark: bool = True, info: dict | None = MAC_INFO) -> sparks.Hub:
    hub = sparks.Hub(home, ROOT, spark, {}, [], threading.Lock(), threading.Event(), lambda: {"version": "4.5.0", "disk_gb": 900},
                     lambda path: home / path, lambda job, worker: None, lambda kind, params: 60)
    hub.keep({"workers": [{"address": MAC, "name": "mac-studio", "folder": "Imagination-Studio", "enabled": True}], "sparks": 2})
    if info is not None:
        hub.state(MAC).update(state="ready", info=dict(info))
    return hub


def test_a_mac_is_given_what_it_renders_with_its_own_files():
    # from a Spark: FastH3 clips, at any size the Mac lists, with a LoRA the Mac has
    assert sparks.capable("video", PLAIN, MAC_INFO) == ""
    assert sparks.capable("video", {"prompt": "x", "quality": "fh4"}, MAC_INFO) == ""  # no preset: the Spark's own, Small
    assert sparks.capable("video", {**PLAIN, "loras": [{"id": "camera-motion"}]}, MAC_INFO) == ""
    assert "LoRA other" in sparks.capable("video", {**PLAIN, "loras": [{"id": "other"}]}, MAC_INFO)
    assert "FastH3 is not installed" in sparks.capable("video", PLAIN, {**MAC_INFO, "fasth3": False})
    # a Spark's text encoder file is a ComfyUI file: a Mac does not read it, whatever it is called
    said = sparks.capable("video", {**PLAIN, "text_encoder": "/home/you/ComfyUI/models/text_encoders/other_h3_mlx.safetensors"}, MAC_INFO)
    assert "this Spark's" in said and "MLX" in said
    # from a Mac to a Mac the two have the same settings: Draft, Turbo's qualities and a text encoder both have, by name
    assert sparks.capable("video", {"prompt": "x", "quality": "standard"}, MAC_INFO, spark=False) == ""
    assert sparks.capable("video", {**PLAIN, "preset": "qhd", "text_encoder": "/Users/you/h3/other_h3_mlx.safetensors"}, MAC_INFO, spark=False) == ""
    assert "not on that Mac" in sparks.capable("video", {**PLAIN, "text_encoder": "/Users/you/h3/mine.safetensors"}, MAC_INFO, spark=False)
    assert sparks.capable("video", {**PLAIN, "quality": "standard"}, {**MAC_INFO, "qualities": ["fh8", "fh4", "fh20"]}, spark=False) == "the standard quality is not rendered there"
    # only single clips, as to a Spark
    assert sparks.capable("long", PLAIN, MAC_INFO)


def test_the_page_calls_it_a_mac_and_the_time_is_not_called_measured():
    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_mac(Path(scratch))
        row = hub.overview()["workers"][0]
        assert row["platform"] == "mac" and row["usable"] and row["memory_gb"] == 190.0
        assert hub.team({"kind": "video", "params": PLAIN, "sparks": {"chosen": 2}}) == [MAC]
        said = hub.plan({"sparks": 2, "kind": "video", "clips": 8, "params": PLAIN})
        assert said["used"] == 2 and said["count"] == "2 machines" and "One clip per machine" in said["says"]
        assert said["estimate"]["measured"] is False and "a Mac and a Spark" in said["estimate"]["note"]
        assert "never split across machines" in hub.plan({"sparks": 2, "kind": "video", "clips": 1, "params": PLAIN})["says"]
        # a list of Sparks alone keeps its words
        hub.state(MAC)["info"] = {**MAC_INFO, "platform": "spark"}
        assert hub.plan({"sparks": 2, "kind": "video", "clips": 8, "params": PLAIN})["count"] == "2 Sparks"


def answers(found: dict):
    """A worker's ssh, standing in: the probe's lines for ``found``, and every command written down."""

    seen = []

    def shell(address, command, timeout=30, via=None):
        seen.append(command)
        if 'echo "host' in command:
            return 0, "\n".join(f"{key} {value}" for key, value in found.items())
        return 0, ""
    return seen, shell


def test_a_mac_is_looked_at_without_proc_and_told_apart_by_uname():
    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_mac(Path(scratch), info=None)
        worker = hub.workers()[0]
        # no Studio there yet: the install it is pointed to is the Mac's
        seen, hub.shell = answers({"host": "mac-studio", "os": "Darwin", "version": ""})
        live = hub.check(worker)
        assert live["state"] == "bare" and "oneshot-setup.sh on that Mac" in live["why"] and "spark" not in live["why"].lower().replace("this spark", "")
        assert hub.platform(MAC) == "mac" and hub.overview()["workers"][0]["platform"] == "mac"
        probe = seen[0]
        assert "uname -s" in probe and "/proc/meminfo 2>/dev/null" in probe  # a Mac has no /proc: that line says nothing there
        assert f'bash -c "(exec 3<>/dev/tcp/127.0.0.1/{sparks.PORT}) 2>/dev/null"' in probe  # zsh, a Mac's login shell, has no /dev/tcp
        # installed, its app not running
        seen, hub.shell = answers({"host": "mac-studio", "os": "Darwin", "version": "4.5.0", "pack": "yes", "install": "yes"})
        assert hub.check(worker)["state"] == "stopped"
        # a Spark is still a Spark
        seen, hub.shell = answers({"host": "spark-5", "os": "Linux", "version": ""})
        assert "oneshot-setup-spark.sh on that Spark" in hub.check(worker)["why"] and hub.platform(MAC) == "spark"


def test_a_macs_own_app_is_never_written_over_or_stopped():
    """The app its owner started on a Mac (no pid file of a head's): level with this code it is used as it is; behind,
    nothing is sent and nothing is stopped, and the row says where to update it."""

    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_mac(Path(scratch), info=None)
        worker = hub.workers()[0]
        seen, hub.shell = answers({"host": "mac-studio", "os": "Darwin", "version": "4.5.0", "pack": "yes", "install": "yes", "app": "yes"})
        copied = []
        hub.copy = lambda *a, **k: (copied.append(a), (0, ""))[1]
        hub.connect = lambda address: "http://127.0.0.1:9"
        said = {"/api/spark/self": {**MAC_INFO, "code": hub.code(), "active": 0}, "/api/jobs": []}
        real, sparks.call = sparks.call, lambda base, path, body=None, method=None, timeout=10: said[path]
        try:
            live = hub.update(worker)
            assert live["state"] == "ready" and not copied
            # its owner keeps the code and the environment where they like: an app that answers needs neither found
            seen, hub.shell = answers({"host": "mac-studio", "os": "Darwin", "version": "", "app": "yes"})
            assert hub.check(worker)["state"] == "ready"
            assert not any("scripts/app.sh" in command or "kill" in command.replace("kill -0", "") for command in seen)
            # behind this Spark's code: still nothing sent, and the way out is said
            said["/api/spark/self"] = {**MAC_INFO, "code": {**hub.code(), next(iter(hub.code())): "other"}, "active": 0}
            live = hub.update(worker)
            assert live["state"] == "update" and not copied and "runs its own Studio app" in live["why"] and "Machine → Updates" in live["why"]
            assert hub.team({"kind": "video", "params": PLAIN, "sparks": {"chosen": 2}}) == []  # and no clip goes to it meanwhile
            # its memory manager turned off there: no guard, no clip
            said["/api/spark/self"] = {**MAC_INFO, "code": hub.code(), "guard": False, "active": 0}
            live = hub.check(worker)
            assert live["state"] == "update" and "without its memory guard" in live["why"]
            # Stop leaves it running too
            assert "was not started by this Studio" in hub.stop(worker)["why"] and "that Mac" in hub.state(MAC)["why"]
        finally:
            sparks.call = real
        # an app a head started there (its pid file is found) is updated as on a Spark: code sent, stopped, started
        seen, hub.shell = answers({"host": "mac-studio", "os": "Darwin", "version": "4.4.0", "pack": "yes", "install": "yes", "app": "yes", "ours": "yes"})
        hub.connect = lambda address: (_ for _ in ()).throw(OSError("no tunnel"))
        sleep, time.sleep = time.sleep, lambda seconds: None
        try:
            hub.update(worker)
        finally:
            time.sleep = sleep
        assert len(copied) == 1 and any("scripts/app.sh" in command and "setsid" in command for command in seen)


def test_a_clip_for_a_mac_names_the_size_a_spark_would_render():
    with tempfile.TemporaryDirectory() as scratch:
        home = Path(scratch)
        hub = hub_with_a_mac(home)
        hub.connect = lambda address: "http://127.0.0.1:9"
        sent = {}

        def call(base, path, body=None, method=None, timeout=10):
            if path == "/api/estimate":
                return {"seconds": None, "memory": None}  # a Mac's guard answers when its job starts (memory_manager.admit)
            if path == "/api/jobs":
                sent.update(body)
                return {"id": "m1"}
            return {"status": "done", "outputs": ["renders/m1.mp4"], "elapsed": 41, "log": ""}
        real_call, real_fetch = sparks.call, sparks.remote.fetch
        sparks.call, sparks.remote.fetch = call, lambda base, path, target, timeout=300, key=None: target.write_bytes(b"clip")
        try:
            job = {"id": "j1", "kind": "video", "title": "t", "status": "running", "log": "", "params": {"prompt": "x", "quality": "fh8"}}
            assert hub.render(job, hub.workers()[0], dict(job["params"]), home / "j1.mp4") is True
            assert sent["params"]["preset"] == "small" and sent["params"]["quality"] == "fh8" and (home / "j1.mp4").read_bytes() == b"clip"
            assert "it is a Mac" in job["log"] and "the same seed gives other frames" in job["log"]
            # a named size goes as named, and a worker of the head's own kind is told nothing it was not told before
            sent.clear()
            job = {"id": "j2", "kind": "video", "title": "t", "status": "running", "log": "", "params": {"prompt": "x", "preset": "sharp"}}
            hub.render(job, hub.workers()[0], dict(job["params"]), home / "j2.mp4")
            assert sent["params"]["preset"] == "sharp"
            sent.clear()
            hub.state(MAC)["info"] = {**MAC_INFO, "platform": "spark"}
            job = {"id": "j3", "kind": "video", "title": "t", "status": "running", "log": "", "params": {"prompt": "x"}}
            hub.render(job, hub.workers()[0], dict(job["params"]), home / "j3.mp4")
            assert "preset" not in sent["params"] and "it is a" not in job["log"]
        finally:
            sparks.call, sparks.remote.fetch = real_call, real_fetch


def test_the_commands_start_find_and_stop_an_app_in_bash_and_in_zsh():
    """start_command, the probe and stop_command, run for real against a stand-in app: in bash, and in zsh where it is
    installed (a Mac's login shell). The stop goes by ``ps``, which a Spark and a Mac both have."""

    for shell in filter(None, (shutil.which("bash"), shutil.which("zsh"))):
        with tempfile.TemporaryDirectory() as scratch:
            home, folder = Path(scratch), "studio"
            (home / folder / "app").mkdir(parents=True)
            (home / folder / "scripts").mkdir()
            (home / folder / "VERSION").write_text("4.5.0\n")
            (home / folder / "app/server.py").write_text("import os, time\nopen(os.environ['STUDIO_PIDFILE'], 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
            (home / folder / "scripts/app.sh").write_text(f'#!/bin/bash\nexec {sys.executable} app/server.py\n')
            run = lambda command: subprocess.run([shell, "-c", command], capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": str(home)})
            started = run(sparks.Hub.start_command(folder))
            assert started.returncode == 0 and "started" in started.stdout, (shell, started.stderr)
            pidfile = home / "TensorFoldStudio/app.pid"
            until = time.time() + 10
            while time.time() < until and not pidfile.exists():
                time.sleep(0.05)
            pid = int(pidfile.read_text())
            try:
                hub = hub_with_a_mac(home, info=None)
                hub.shell = lambda address, command, timeout=30, via=None: (lambda done: (done.returncode, (done.stdout + done.stderr).strip()))(run(command))
                found = hub.probe({"address": MAC, "folder": folder})
                assert found["reached"] and found["pack"] == "yes" and found["ours"] == "yes" and found["version"] == "4.5.0" and found["os"], (shell, found)
                stopped = run(sparks.Hub.stop_command(folder, comfy=False))
                assert "app stopped" in stopped.stdout, (shell, stopped.stdout, stopped.stderr)
                until = time.time() + 5
                while time.time() < until and Path(f"/proc/{pid}").exists():
                    time.sleep(0.05)
                assert not pidfile.exists()
                # a pid file that names some other program is left alone
                pidfile.write_text(str(os.getpid()))
                assert "no app of ours" in run(sparks.Hub.stop_command(folder, comfy=False)).stdout
            finally:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
