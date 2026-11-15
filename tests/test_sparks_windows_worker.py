"""A Windows PC as a worker: python3 tests/test_sparks_windows_worker.py. A head hands the native Windows Studio single
clips the way it hands them to a Spark. No GPU and no other machine: what goes over ssh is read as the command it is.
Its app is always its owner's: nothing is sent to it, started or stopped from a head."""
import base64
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import sparks  # noqa: E402

PC = "you@192.168.1.30"
# what the native Windows Studio says of itself (server.about_this_spark on an RTX Spark N1X): the CUDA build's sizes and qualities
WIN_INFO = {"platform": "windows", "fasth3": True, "guard": True, "loras": [], "ffmpeg": True, "text_encoders": ["other_minimax_h3.safetensors"],
            "memory": {"available_mb": 97000, "total_mb": 98304, "floor_mb": 2048}, "disk_gb": 110, "code": {},
            "presets": ["small", "native", "p720", "sharp", "custom"], "qualities": ["fh8", "fh4", "fh20"]}
PLAIN = {"prompt": "x", "preset": "small", "quality": "fh8", "frames": 124}


def hub_with_a_pc(home: Path, spark: bool = True, info: dict | None = WIN_INFO) -> sparks.Hub:
    hub = sparks.Hub(home, ROOT, spark, {}, [], threading.Lock(), threading.Event(), lambda: {"version": "4.5.0", "disk_gb": 900},
                     lambda path: home / path, lambda job, worker: None, lambda kind, params: 60)
    hub.keep({"workers": [{"address": PC, "name": "pc", "enabled": True}], "sparks": 2})
    if info is not None:
        hub.state(PC).update(state="ready", info=dict(info))
    return hub


def windows_ssh(lines: list[str]):
    """ssh into cmd.exe: the POSIX probe is not understood there (status 1), PowerShell's answers with ``lines``."""

    seen = []

    def shell(address, command, timeout=30, via=None):
        seen.append(command)
        if command.startswith("powershell "):
            return 0, "\r\n".join(lines)
        return 1, "'F' is not recognized as an internal or external command,\r\noperable program or batch file."
    return seen, shell


def test_a_windows_pc_is_asked_in_powershell_when_no_posix_shell_answers():
    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_pc(Path(scratch), info=None)
        worker = hub.workers()[0]
        seen, hub.shell = windows_ssh(["host KEYSPC", "os Windows", "app yes"])
        found = hub.probe(worker)
        assert found["reached"] and found["os"] == "Windows" and found["host"] == "KEYSPC" and found["app"] == "yes"
        # the second question is one PowerShell command no shell on the way has to quote
        asked = seen[1].split()
        assert asked[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand"] and len(asked) == 5
        script = base64.b64decode(asked[4]).decode("utf-16-le")
        assert script == sparks.WIN_PROBE and f"'127.0.0.1', {sparks.PORT}" in script and "Connect" in script
        assert not any(word in script for word in ("Remove-", "Stop-", "Start-Process", "Set-", "Invoke-"))  # it only looks
        # ssh that did not get in is not asked again: the reason it gave is shown
        calls = []
        hub.shell = lambda address, command, timeout=30, via=None: (calls.append(command), (255, "Permission denied (publickey)."))[1]
        assert hub.check(worker)["state"] == "unreachable" and "Permission denied" in hub.state(PC)["why"] and len(calls) == 1
        # a machine that is neither (no POSIX shell, no PowerShell): unreachable, with what it said
        hub.shell = lambda address, command, timeout=30, via=None: (1, "no such shell")
        assert hub.check(worker)["state"] == "unreachable" and "no such shell" in hub.state(PC)["why"]
        # a Spark or a Mac is never asked the second question
        calls.clear()
        hub.shell = lambda address, command, timeout=30, via=None: (calls.append(command), (0, "host spark-5\nos Linux"))[1]
        hub.probe(worker)
        assert len(calls) == 1


def test_its_app_is_its_owners_nothing_is_sent_started_or_stopped():
    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_pc(Path(scratch), info=None)
        worker = hub.workers()[0]
        copied = []
        hub.copy = lambda *a, **k: (copied.append(a), (0, ""))[1]
        hub.connect = lambda address: "http://127.0.0.1:9"
        # no app open there: said, and "Update and start" starts nothing
        seen, hub.shell = windows_ssh(["host KEYSPC", "os Windows"])
        for act in (hub.check, hub.update):
            live = act(worker)
            assert live["state"] == "stopped" and live["why"] == sparks.WIN_STOPPED and hub.platform(PC) == "windows"
        said = {"/api/spark/self": {**WIN_INFO, "code": hub.code(), "active": 0}}
        real, sparks.call = sparks.call, lambda base, path, body=None, method=None, timeout=10: said[path]
        try:
            # its app open and level with this code: ready, by its address alone (no folder, no key read over ssh)
            seen, hub.shell = windows_ssh(["host KEYSPC", "os Windows", "app yes"])
            assert hub.check(worker)["state"] == "ready" and hub.their_key(PC) == ""
            row = hub.overview()["workers"][0]
            assert row["platform"] == "windows" and row["usable"] and row["name"] == "pc" and row["memory_gb"] == 97.0
            assert hub.team({"kind": "video", "params": PLAIN, "sparks": {"chosen": 2}}) == [PC]
            # behind: nothing sent, the way out said
            said["/api/spark/self"] = {**WIN_INFO, "code": {**hub.code(), next(iter(hub.code())): "other"}, "active": 0}
            live = hub.update(worker)
            assert live["state"] == "update" and "differs from this Spark's in 1 file" in live["why"] and live["why"].endswith(sparks.WIN_OWN)
            assert hub.team({"kind": "video", "params": PLAIN, "sparks": {"chosen": 2}}) == []
            # Stop leaves it running
            assert "is its owner's: it is left running" in hub.stop(worker)["why"]
        finally:
            sparks.call = real
        assert not copied and all(command.startswith("powershell ") or 'echo "host' in command for command in seen)


def test_a_windows_pc_has_a_sparks_settings_and_is_called_what_it_is():
    # the CUDA build: what a Spark is given, by the sizes and qualities it lists; a text encoder by its name
    assert sparks.capable("video", PLAIN, WIN_INFO) == ""
    assert sparks.capable("video", {**PLAIN, "text_encoder": "/home/you/ComfyUI/models/text_encoders/other_minimax_h3.safetensors"}, WIN_INFO) == ""
    assert "not on that Windows PC" in sparks.capable("video", {**PLAIN, "text_encoder": "mine.safetensors"}, WIN_INFO)
    # from a Mac it is held to what a Spark is held to
    assert "MiniMax H3 Turbo" in sparks.capable("video", {**PLAIN, "quality": "standard"}, WIN_INFO, spark=False)
    assert "2x decoder" in sparks.capable("video", {"prompt": "x", "quality": "fh8"}, WIN_INFO, spark=False)
    assert sparks.family("windows") == "spark" and sparks.word_of("windows") == "Windows PC" and sparks.platform_of({"platform": "linux"}) == "spark"
    with tempfile.TemporaryDirectory() as scratch:
        hub = hub_with_a_pc(Path(scratch))
        said = hub.plan({"sparks": 2, "kind": "video", "clips": 8, "params": PLAIN})
        assert said["count"] == "2 machines" and said["estimate"]["measured"] is False and "Windows PC" in said["estimate"]["note"]


def test_code_is_compared_by_its_lines_and_by_one_spelling_of_a_path():
    """Git on Windows checks text out with CRLF ends and Windows spells a path with backslashes: neither is another code."""

    with tempfile.TemporaryDirectory() as scratch:
        here, there = Path(scratch) / "here", Path(scratch) / "there"
        for pack, end in ((here, b"\n"), (there, b"\r\n")):
            (pack / "app").mkdir(parents=True)
            (pack / "VERSION").write_bytes(b"4.5.0" + end)
            (pack / "app/server.py").write_bytes(b"import os" + end + b"print(1)" + end)
        mine, theirs = sparks.digest(here), sparks.digest(there)
        assert mine == theirs and set(mine) == {"VERSION", "app/server.py"}
        (there / "app/server.py").write_bytes(b"import os\r\nprint(2)\r\n")
        assert sparks.differs(mine, sparks.digest(there)) == ["app/server.py"]


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
