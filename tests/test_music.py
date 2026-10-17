"""Music (MiniMax Music 3): python3 tests/test_music.py. No GPU, no model: the engine and ComfyUI are stood in for.
The soundtrack tests need ffmpeg and are skipped without it."""
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ["STUDIO_GPU_LOCK"] = str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d")
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import server  # noqa: E402
import music  # noqa: E402
import voice  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
voice.LOCK = Path(os.environ["STUDIO_GPU_LOCK"])

# `python -I -m tensorfold.music3 --model M --serve`, stood in for: the protocol of TensorFold's music3-mlx (6844b413)
MLX_SERVE = r"""
import json, os, struct, sys, wave
args = sys.argv[1:]
assert args[:3] == ["-I", "-m", "tensorfold.music3"] and args[-1] == "--serve" and "--model" in args
log = open(os.environ["FAKE_MUSIC3_LOG"], "a")
print("[music3] MiniMax-Music3 runs locally", file=sys.stderr, flush=True)
print(json.dumps({"ready": True, "engine": "tensorfold-music3-mlx", "load_s": 0.1, "sample_rate": 44100, "channels": 2,
                  "license_display": "MiniMax-Music3"}), file=sys.stderr, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    log.write(line); log.flush()
    if "FAIL" in request["caption"]:
        print(json.dumps({"ok": False, "error": "ValueError: the prompt has 6000 tokens", "id": request.get("id")}), file=sys.stderr, flush=True)
        continue
    if request.get("progress"):
        for stage, total in (("ar", 50), ("dit", 30), ("dav", 1)):
            print(json.dumps({"id": request["id"], "progress": {"stage": stage, "done": total, "total": total}}), file=sys.stderr, flush=True)
    seconds = 2.0
    with wave.open(request["wav"], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(struct.pack("<hh", 100, -100) * int(44100 * seconds))
    print(json.dumps({"ok": True, "id": request["id"], "wav": request["wav"], "audio_s": seconds, "frames": 50, "eos": True,
                      "ar_ms": 1500.0, "dit_ms": 800.0, "dav_ms": 100.0, "total_ms": 2400.0, "rtf": 1.2, "first_audio_ms": 2400.0,
                      "stream": False, "seed": request["seed"], "peak_memory_gb": 34.9}), file=sys.stderr, flush=True)
"""


def fake_mlx(folder: Path) -> "music.Mlx":
    python = folder / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!{sys.executable}\n" + MLX_SERVE)
    python.chmod(0o755)
    os.environ.update(MUSIC3_VENV=str(folder / "venv"), MUSIC3_MODEL=str(folder / "model"), FAKE_MUSIC3_LOG=str(folder / "requests.jsonl"))
    try:
        one = music.Mlx()
    finally:
        for key in ("MUSIC3_VENV", "MUSIC3_MODEL"):
            os.environ.pop(key)
    one.known, one.NEED_GB = (time.time(), ""), 0
    return one


def requests_of(folder: Path) -> list[dict]:
    file = folder / "requests.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines()] if file.is_file() else []


def wait(job_id: str, timeout: float = 30) -> dict:
    started = time.time()
    while time.time() - started < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def test_the_mac_engine_runs_int8_weights_unless_told_otherwise():
    old = os.environ.pop("MUSIC3_QUANT", None)
    try:
        argv = music.Mlx().argv()
        assert argv[argv.index("--quant") + 1] == "int8" and argv[-1] == "--serve"
        os.environ["MUSIC3_QUANT"] = "none"
        argv = music.Mlx().argv()
        assert argv[argv.index("--quant") + 1] == "none"
    finally:
        os.environ.pop("MUSIC3_QUANT", None)
        if old is not None:
            os.environ["MUSIC3_QUANT"] = old


def test_a_song_is_checked_before_it_is_queued():
    had = music._engine
    try:
        music._engine = fake_mlx(Path(tempfile.mkdtemp()))
        for body, said in (({"lyrics": "[Verse]\nla la"}, "style"), ({"caption": "pop", "seconds": 5}, "10 to 300"),
                           ({"caption": "pop", "seconds": 301}, "10 to 300"), ({"caption": "pop", "steps": 200}, "steps"),
                           ({"caption": "x" * 2001}, "at most"), ({"caption": "pop", "seed": "seven"}, "numbers")):
            answer = client.post("/api/music/takes", json=body)
            assert answer.status_code == 400 and said in answer.json()["error"], (body, answer.json())
        params = music.checked({"caption": "  dreamy   synth-pop ", "lyrics": "", "seconds": 30, "seed": 3})
        assert params["caption"] == "dreamy synth-pop" and params["lyrics"] == "[Instrumental]" and params["seconds"] == 30.0
        assert params["steps"] == 30 and params["dit_cfg"] == 1.7 and params["ar_cfg"] == 1.5 and params["top_k"] == 50
        assert music.sung("[Verse]\nOne two\n[Chorus]\n(three) four") == "One two (three) four"
        assert music.tags("[Intro]\n[Verse 2]\nla") == ["Intro", "Verse 2"]
        # Whisper against the lyrics: in order, up to where the song got
        said = music.heard("[Verse]\nOne two three four\n[Chorus]\nfive six seven eight nine ten", "one two tree four five six")
        assert (said["found"], said["words"], said["score"], said["reached"], said["total"]) == (5, 6, 83, 60, 10), said
        assert music.heard("[Verse]\nla", "")["score"] == 0
        state = client.get("/api/state").json()["music"]
        assert state["credit"] == "MiniMax-Music3" and "MiniMax-Music3 Community License" in state["licence"]
        assert "US$20 million" in state["licence"] and state["seconds"] == [10, 300] and "Chorus" in state["sections"]
    finally:
        music._engine = had


def test_a_song_runs_on_the_served_engine_holds_the_lock_and_is_unloaded_before_other_jobs():
    folder = Path(tempfile.mkdtemp())
    one = fake_mlx(folder)
    had, had_ready, built = music._engine, music.qa.ready, server.build
    try:
        music._engine = one
        music.qa.ready = lambda: {"transcript": False, "runs": True, "missing": []}
        job = client.post("/api/music/takes", json={"caption": "Hip-hop anthem, 92 BPM", "lyrics": "[Verse]\nRaise it up", "seconds": 60, "seed": 7}).json()
        assert job["kind"] == "music" and job["estimate"] > 60
        done = wait(job["id"])
        assert done["status"] == "done", done
        take = done["music"]
        assert take["route"] == "mlx" and take["duration"] == 2.0 and take["ended"] is True and take["peak_memory_gb"] == 34.9
        assert take["ar_s"] == 1.5 and take["dit_s"] == 0.8 and take["dav_s"] == 0.1 and take["load_s"] is not None and take["credit"] == "MiniMax-Music3"
        assert take["asr"]["skipped"].startswith("no speech-to-text tool")
        sent = requests_of(folder)[-1]
        assert sent["caption"] == "Hip-hop anthem, 92 BPM" and sent["lyrics"] == "[Verse]\nRaise it up" and sent["seconds"] == 60.0
        assert sent["seed"] == 7 and sent["steps"] == 30 and sent["dit_cfg"] == 1.7 and sent["ar_cfg"] == 1.5 and sent["top_k"] == 50
        assert sent["progress"] is True and sent["wav"].endswith(f"{job['id']}.wav") and sent["id"] == job["id"]
        assert (server.HOME / take["path"]).is_file() and client.get(f"/api/music/takes/{job['id']}/audio").status_code == 200
        assert [t["id"] for t in client.get("/api/music/takes").json()][0] == job["id"]
        # loaded after the song, under the GPU lock: the next song does not load again
        assert one.loaded() and voice.lock_owner().startswith("studio-music music3")
        pid = one.process.pid
        again = wait(client.post("/api/music/takes", json={"caption": "pop", "seconds": 10}).json()["id"])
        assert again["status"] == "done" and one.process.pid == pid and again["music"]["load_s"] is None
        # an engine error fails the song and keeps the server
        failed = wait(client.post("/api/music/takes", json={"caption": "FAIL", "seconds": 10}).json()["id"])
        assert failed["status"] == "failed" and "6000 tokens" in failed["error"] and one.loaded()
        # any other job of this machine's queue unloads it first, and the lock goes back
        seen = {}

        def build(kind, params, job_id, room=True):
            seen.update(loaded=one.loaded(), locked=voice.LOCK.exists())
            out = server.HOME / "renders" / f"{job_id}.png"
            return [sys.executable, "-c", f"open({str(out)!r}, 'wb').write(b'png')"], {}, [str(out)]

        server.build = build
        other = {"id": "test-music-yield", "kind": "image", "status": "running", "title": "t", "params": {}, "log": "", "outputs": [],
                 "started": time.time(), "finished": None, "remote": None}
        server.perform(other)
        assert other["status"] == "done" and seen == {"loaded": False, "locked": False}
        assert "MiniMax Music 3 was unloaded" in other["log"] and one.events[-1]["why"] == "a image job needs the GPU"
        assert music.release("again") is False
    finally:
        server.build = built
        one.release("test over")
        music._engine, music.qa.ready = had, had_ready


def test_a_voice_take_and_a_song_never_stand_together():
    """Drama 3 loaded after a take is unloaded before a song, and the song's engine before the next take."""

    folder = Path(tempfile.mkdtemp())
    one = fake_mlx(folder)
    had_music, had_voice = music._engine, voice._backend

    class Loaded:
        name, label = "mlx", "stand-in"
        released = []

        def release(self, why):
            self.released.append(why)
            return True

    try:
        music._engine, voice._backend = one, Loaded()
        done = wait(client.post("/api/music/takes", json={"caption": "pop", "seconds": 10, "asr": False}).json()["id"])
        assert done["status"] == "done" and Loaded.released == ["a music job needs the GPU"]
        assert "Drama 3 was unloaded" in done["log"]
        assert one.loaded()
        job = {"id": "test-voice-after-music", "kind": "voice", "status": "running", "title": "t", "params": {}, "log": "", "outputs": [],
               "started": time.time(), "finished": None, "remote": None}
        had_perform = server.voice_perform
        server.voice_perform = lambda job: True
        try:
            server.perform(job)
        finally:
            server.voice_perform = had_perform
        assert not one.loaded() and "MiniMax Music 3 was unloaded" in job["log"]
    finally:
        one.release("test over")
        music._engine, voice._backend = had_music, had_voice


def test_the_spark_route_runs_comfy_holds_the_lock_while_loaded_and_frees_it():
    folder = Path(tempfile.mkdtemp())
    script = folder / "fake_generate.py"
    script.write_text(r"""
import json, struct, sys, wave
args = sys.argv[1:]
out = args[args.index("-o") + 1]
open(sys.argv[0] + ".kind", "w").write("music")
for i in (10, 50): print(f"[tensorfold] ar {i}/50", flush=True)
for i in (15, 30): print(f"[tensorfold] dit {i}/30", flush=True)
with wave.open(out, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(struct.pack("<hh", 50, 50) * 44100 * 3)
print("[tensorfold] " + json.dumps({"output": out, "audio_s": 3.0, "ar_s": 20.0, "dit_s": 9.0, "dav_s": 0.5, "load_s": 12.0,
                                    "total_s": 31.0, "eos": True, "peak_dbfs_decoded": 0.1, "limited": True}), flush=True)
""")

    class Fake(music.Comfy):
        freed = []

        def command(self, params, caption, lyrics, wav):
            self.sent = {"caption": caption.read_text(), "lyrics": lyrics.read_text(), "params": dict(params)}
            return [sys.executable, str(script), "-o", str(wav)]

        def kind(self):
            return (script.parent / "fake_generate.py.kind").read_text().strip() if (script.parent / "fake_generate.py.kind").exists() else ""

        def free(self):
            self.freed.append(time.time())
            (script.parent / "fake_generate.py.kind").unlink(missing_ok=True)
            return True

        def check(self):
            return ""

    had, had_up = music._engine, voice.comfy_up
    one = Fake()
    one.idle_s = 1
    try:
        music._engine, voice.comfy_up = one, (lambda port=None: True)
        done = wait(client.post("/api/music/takes", json={"caption": "lo-fi", "lyrics": "[Verse]\nhello", "seconds": 30, "asr": False}).json()["id"])
        assert done["status"] == "done", done
        take = done["music"]
        assert take["route"] == "comfy" and take["duration"] == 3.0 and take["ar_s"] == 20.0 and take["load_s"] == 12.0
        assert one.sent["caption"] == "lo-fi\n" and one.sent["lyrics"] == "[Verse]\nhello\n"
        assert one.loaded() and voice.lock_owner().startswith("studio-music comfy")
        # idle: freed with ComfyUI's /free, the lock given back
        started = time.time()
        while (one.owner or voice.LOCK.exists()) and time.time() - started < 20:
            time.sleep(0.2)
        assert one.freed and not one.owner and not voice.LOCK.exists() and "idle" in one.events[-1]["why"]
    finally:
        one.release("test over")
        music._engine, voice.comfy_up = had, had_up


# `music3-generate --model M --weights W --serve 1`, stood in for: TensorFold's CUDA engine (music3-cuda 6b714531)
# speaks the Mac engine's protocol, with "engine":"tensorfold-music3-cuda" and "weights" on its ready line
CUDA_SERVE = r"""
import json, os, struct, sys, wave
args = sys.argv[1:]
assert args[-2:] == ["--serve", "1"] and "--model" in args and args[args.index("--weights") + 1] in ("int8", "bf16"), args
log = open(os.environ["FAKE_MUSIC3_LOG"], "a")
log.write(json.dumps({"argv": args, "ld": os.environ.get("LD_LIBRARY_PATH", "")}) + "\n"); log.flush()
print("[music3] loading MiniMax-Music3 (int8 made on the GPU)", file=sys.stderr, flush=True)
print(json.dumps({"ready": True, "engine": "tensorfold-music3-cuda", "load_s": 0.1, "sample_rate": 44100, "channels": 2,
                  "license_display": "MiniMax-Music3", "weights": args[args.index("--weights") + 1]}), file=sys.stderr, flush=True)
for line in sys.stdin:
    if not line.startswith("{"):
        print(json.dumps({"ok": False, "error": "bad json"}), file=sys.stderr, flush=True)
        continue
    request = json.loads(line)
    log.write(line); log.flush()
    if "FAIL" in request["caption"]:
        print(json.dumps({"ok": False, "error": "the prompt has 6000 tokens", "id": request.get("id")}), file=sys.stderr, flush=True)
        continue
    for stage, total in (("ar", 1500), ("dit", 30), ("dav", 1)):
        print(json.dumps({"id": request["id"], "progress": {"stage": stage, "done": total, "total": total}}), file=sys.stderr, flush=True)
    seconds = 2.0
    with wave.open(request["wav"], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(struct.pack("<hh", 100, -100) * int(44100 * seconds))
    open(request["stats"], "w").write(json.dumps({"engine": "tensorfold-music3-cuda", "ar_s": 3.0}))
    print(json.dumps({"ok": True, "id": request["id"], "wav": request["wav"], "audio_s": seconds, "frames": 50, "eos": False,
                      "ar_ms": 3000.0, "dit_ms": 1900.0, "dav_ms": 200.0, "total_ms": 5100.0, "rtf": 2.55, "first_audio_ms": 5100.0,
                      "stream": False, "seed": request["seed"], "peak_memory_gb": 18.1}), file=sys.stderr, flush=True)
"""


def cuda_install(folder: Path, binary: bool = True, weights: bool = True) -> None:
    """A Spark's install under ``folder``: $PREFIX/music3/bin/music3-generate (the stand-in) and the three weight files."""

    if binary:
        engine = folder / "prefix" / "music3" / "bin" / "music3-generate"
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_text(f"#!{sys.executable}\n" + CUDA_SERVE)
        engine.chmod(0o755)
    if weights:
        for part in music.WEIGHTS:
            (folder / "models" / "MiniMax-Music3-Comfy" / part).parent.mkdir(parents=True, exist_ok=True)
            (folder / "models" / "MiniMax-Music3-Comfy" / part).write_bytes(b"w")


class Spark:
    """music.py as on a DGX Spark with its install under ``folder`` (PREFIX, SPARK_MODELS), the chosen engine reset."""

    def __init__(self, folder: Path, **env):
        self.folder, self.env = folder, {"SPARK_MODELS": str(folder / "models"), "FAKE_MUSIC3_LOG": str(folder / "requests.jsonl"), **env}

    def __enter__(self):
        self.had = (music.SPARK, music.PREFIX, music._engine, music._chosen_why, {k: os.environ.get(k) for k in self.env})
        music.SPARK, music.PREFIX, music._engine = True, self.folder / "prefix", None
        for key in ("MUSIC3_BACKEND", "MUSIC3_CUDA_BIN", "MUSIC3_MODEL", "MUSIC3_WEIGHTS"):
            self.had[4].setdefault(key, os.environ.get(key))
            os.environ.pop(key, None)
        os.environ.update(self.env)
        return self

    def __exit__(self, *_):
        if music._engine is not None and hasattr(music._engine, "release"):
            music._engine.release("test over")
        music.SPARK, music.PREFIX, music._engine, music._chosen_why, saved = self.had
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_spark_prefers_the_cuda_engine_and_falls_back_to_comfy():
    folder = Path(tempfile.mkdtemp())
    cuda_install(folder)
    with Spark(folder):
        one = music.engine()
        assert isinstance(one, music.Cuda) and one.name == "cuda" and music._chosen_why == ""
        assert one.bin == folder / "prefix" / "music3" / "bin" / "music3-generate"
        assert one.model == folder / "models" / "MiniMax-Music3-Comfy" and one.present() == ""
        argv = one.argv()
        assert argv[0] == str(one.bin) and argv[argv.index("--weights") + 1] == "int8" and argv[-2:] == ["--serve", "1"]
        assert one.guard_route == "music-cuda-int8" and one.RTF == 2.1 and one.NEED_GB == 30.0
        assert one.estimate(60) == int(37 + 60 * 2.1 + 2)
    with Spark(folder, MUSIC3_WEIGHTS="bf16"):
        one = music.engine()
        assert one.weights == "bf16" and one.argv()[one.argv().index("--weights") + 1] == "bf16" and one.RTF == 3.3
        assert one.guard_route == "music-cuda-bf16"
    with Spark(folder, MUSIC3_BACKEND="comfy"):  # told: ComfyUI's nodes, the engine built or not
        assert isinstance(music.engine(), music.Comfy) and music._chosen_why == ""
    other = Path(tempfile.mkdtemp())
    with Spark(folder, MUSIC3_CUDA_BIN=str(other / "music3-generate")):  # no binary: ComfyUI, and the state says why
        assert isinstance(music.engine(), music.Comfy) and "No MiniMax Music 3 engine" in music._chosen_why
    cuda_install(other, binary=True, weights=False)
    with Spark(other):  # the binary without the weights: ComfyUI too
        assert isinstance(music.engine(), music.Comfy) and "weights" in music._chosen_why
    with Spark(other, MUSIC3_BACKEND="cuda"):  # told cuda without the weights: the tab says what is missing
        one = music.engine()
        assert isinstance(one, music.Cuda) and "No MiniMax Music 3 weights" in one.check()
        answer = client.post("/api/music/takes", json={"caption": "pop", "seconds": 10})
        assert answer.status_code == 400 and "weights" in answer.json()["error"]
    had = music.SPARK
    try:
        music.SPARK = False
        assert music.choose()[0] is music.Mlx
    finally:
        music.SPARK = had


def test_the_cuda_engine_makes_a_song_stops_comfy_first_and_is_unloaded_before_other_jobs():
    folder = Path(tempfile.mkdtemp())
    cuda_install(folder)
    had_up, had_ready, built = voice.comfy_up, music.qa.ready, server.build
    comfy = {"up": True, "stopped": 0}
    with Spark(folder):
        one = music.engine()
        assert isinstance(one, music.Cuda)
        one.NEED_GB, one.known = 0, (time.time(), "")
        one.comfy_holds = lambda: "video" if comfy["up"] else ""

        def stop_comfy():
            comfy.update(up=False, stopped=comfy["stopped"] + 1)

        one.stop_comfy = stop_comfy
        try:
            voice.comfy_up = lambda port=None: comfy["up"]
            music.qa.ready = lambda: {"transcript": False, "runs": True, "missing": []}
            state = client.get("/api/music/state").json()
            assert state["route"] == "cuda" and state["label"].startswith("TensorFold CUDA") and state["weights"] == "int8"
            assert state["fallback"] is None and state["rtf"] == 2.1 and state["credit"] == "MiniMax-Music3"
            job = client.post("/api/music/takes", json={"caption": "Hip-hop anthem, 92 BPM", "lyrics": "[Verse]\nRaise it up", "seconds": 60, "seed": 7}).json()
            assert job["kind"] == "music" and job["estimate"] >= int(37 + 60 * 2.1)
            done = wait(job["id"])
            assert done["status"] == "done", done
            take = done["music"]
            assert take["route"] == "cuda" and take["duration"] == 2.0 and take["ended"] is False and take["peak_memory_gb"] == 18.1
            assert take["ar_s"] == 3.0 and take["dit_s"] == 1.9 and take["dav_s"] == 0.2 and take["load_s"] is not None
            assert take["credit"] == "MiniMax-Music3" and (server.HOME / take["path"]).is_file()
            # ComfyUI held the video models: stopped before the engine loaded, once
            assert comfy["stopped"] == 1 and any(e["what"] == "stopped ComfyUI before loading" for e in one.events)
            assert one.ready["engine"] == "tensorfold-music3-cuda" and one.ready["weights"] == "int8"
            started, sent = requests_of(folder)[0], requests_of(folder)[-1]
            assert started["argv"][started["argv"].index("--model") + 1] == str(folder / "models" / "MiniMax-Music3-Comfy")
            if Path("/usr/local/cuda/lib64").is_dir():
                assert "/usr/local/cuda/lib64" in started["ld"]
            assert sent["caption"] == "Hip-hop anthem, 92 BPM" and sent["seconds"] == 60.0 and sent["seed"] == 7 and sent["progress"] is True
            assert sent["steps"] == 30 and sent["dit_cfg"] == 1.7 and sent["ar_cfg"] == 1.5 and sent["top_k"] == 50
            assert Path(sent["stats"]).is_file()
            # loaded after the song, under the GPU lock; its memory counts as the next job's (music.room_mb)
            assert one.loaded() and voice.lock_owner().startswith("studio-music music3-cuda")
            assert music.room_mb() == int(one.before_gb * 1024)
            pid = one.process.pid
            again = wait(client.post("/api/music/takes", json={"caption": "pop", "seconds": 10}).json()["id"])
            assert again["status"] == "done" and one.process.pid == pid and again["music"]["load_s"] is None and comfy["stopped"] == 1
            failed = wait(client.post("/api/music/takes", json={"caption": "FAIL", "seconds": 10}).json()["id"])
            assert failed["status"] == "failed" and "6000 tokens" in failed["error"] and one.loaded()
            # any other job of this machine's queue unloads it first, and the lock goes back
            seen = {}

            def build(kind, params, job_id, room=True):
                seen.update(loaded=one.loaded(), locked=voice.LOCK.exists())
                out = server.HOME / "renders" / f"{job_id}.png"
                return [sys.executable, "-c", f"open({str(out)!r}, 'wb').write(b'png')"], {}, [str(out)]

            server.build = build
            other = {"id": "test-music-cuda-yield", "kind": "image", "status": "running", "title": "t", "params": {}, "log": "",
                     "outputs": [], "started": time.time(), "finished": None, "remote": None}
            server.perform(other)
            assert other["status"] == "done" and seen == {"loaded": False, "locked": False}
            assert "MiniMax Music 3 was unloaded" in other["log"] and music.room_mb() is None
        finally:
            server.build, voice.comfy_up, music.qa.ready = built, had_up, had_ready


def test_the_cuda_engine_unloads_when_idle_and_gives_the_lock_back():
    folder = Path(tempfile.mkdtemp())
    cuda_install(folder)
    had_up = voice.comfy_up
    with Spark(folder, MUSIC3_IDLE_S="1"):
        one = music.engine()
        one.NEED_GB, one.known = 0, (time.time(), "")
        try:
            voice.comfy_up = lambda port=None: False
            done = wait(client.post("/api/music/takes", json={"caption": "lo-fi", "seconds": 10, "asr": False}).json()["id"])
            assert done["status"] == "done" and done["music"]["route"] == "cuda"
            assert not any(e["what"] == "stopped ComfyUI before loading" for e in one.events)  # nothing to stop
            started = time.time()
            while (one.loaded() or voice.LOCK.exists()) and time.time() - started < 20:
                time.sleep(0.2)
            assert not one.loaded() and not voice.LOCK.exists() and "idle" in one.events[-1]["why"]
        finally:
            voice.comfy_up = had_up


def test_the_comfy_route_still_runs_where_the_engine_is_not_built():
    folder = Path(tempfile.mkdtemp())
    cuda_install(folder, binary=False)
    with Spark(folder):
        one = music.engine()
        assert isinstance(one, music.Comfy)
        state = client.get("/api/music/state").json()
        assert state["route"] == "comfy" and "No MiniMax Music 3 engine" in state["fallback"] and state["rtf"] == 4.4
        command = one.command({"seconds": 60, "seed": 7, "steps": 30, "dit_cfg": 1.7, "ar_cfg": 1.5, "top_k": 50},
                              Path("c.txt"), Path("l.txt"), Path("o.wav"))
        assert command[1].endswith("spark_generate.py") and command[2] == "music" and "--seconds" in command
    test_the_spark_route_runs_comfy_holds_the_lock_while_loaded_and_frees_it()


def test_the_comfy_workflow_is_the_blueprints():
    import spark_generate

    w = spark_generate.music_workflow("pop", "[Verse]\nla", 60, 7)
    classes = {k: v["class_type"] for k, v in w.items()}
    assert classes == {"1": "UNETLoader", "3": "CLIPLoader", "4": "VAELoader", "5": "MiniMaxMusic3TextEncode",
                       "6": "EmptyMiniMaxMusic3LatentAudio", "7": "ConditioningZeroOut", "12": "KSampler",
                       "13": "VAEDecodeAudioTiled", "17": "AudioAdjustVolume", "18": "SaveAudio"}
    assert w["3"]["inputs"]["type"] == "minimax" and w["3"]["inputs"]["clip_name"].endswith("pruned_bf16.safetensors")
    assert w["5"]["inputs"]["max_duration"] == 60 and w["5"]["inputs"]["seed"] == 7 and w["5"]["inputs"]["cfg_scale"] == 1.5
    assert w["6"]["inputs"]["seconds"] == ["5", 1]  # the length the autoregressive model chose
    sampler = w["12"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (30, 1.7, "euler", "simple")
    assert sampler["negative"] == ["7", 0] and sampler["seed"] == 7
    assert w["13"]["inputs"]["tile_size"] == 1536 and w["17"]["inputs"]["volume"] == -spark_generate.HEADROOM_DB


def test_the_memory_guard_knows_a_song():
    import memory_guard

    fits = memory_guard.music_check(60, available_mb=118_000, total_mb=124_500)
    assert fits["fits"] and fits["route"] == "music" and fits["need_mb"] < 60_000
    refused = memory_guard.music_check(300, available_mb=30_000, total_mb=124_500)
    assert not refused["fits"] and "refused" in refused["reason"]
    fixed, per_second = memory_guard.MUSIC_ESTIMATE
    for asked, _, peak in memory_guard.MUSIC_MEASURED:  # the line holds every measured song, ERROR_MB around it
        assert abs(fixed + per_second * asked - peak) <= memory_guard.ERROR_MB, (asked, peak)
    assert memory_guard.music_check(10, total_mb=0)["fits"]  # a Mac
    # TensorFold's CUDA engine: its own line a weight kind, held to its measured songs the same way
    for route, measured in memory_guard.MUSIC_CUDA_MEASURED.items():
        fixed, per_second = memory_guard.MUSIC_CUDA_ESTIMATE[route]
        for asked, _, peak in measured:
            assert abs(fixed + per_second * asked - peak) <= memory_guard.ERROR_MB, (route, asked, peak)
        found = memory_guard.music_check(60, available_mb=118_000, total_mb=124_500, route=route)
        assert found["fits"] and found["route"] == route and found["need_mb"] == fixed + per_second * 60
    int8 = memory_guard.music_check(60, available_mb=118_000, total_mb=124_500, route="music-cuda-int8")["need_mb"]
    assert int8 < memory_guard.music_check(60, available_mb=118_000, total_mb=124_500, route="music-cuda-bf16")["need_mb"]
    assert int8 < memory_guard.music_check(60, available_mb=118_000, total_mb=124_500)["need_mb"]  # less than ComfyUI's
    # a tight machine: a 60 s song fits there, a 300 s one on 30 GB does not
    tight = memory_guard.music_check(60, available_mb=50_000, total_mb=124_500, route="music-cuda-int8")
    assert tight["fits"] and not memory_guard.music_check(300, available_mb=30_000, total_mb=124_500, route="music-cuda-int8")["fits"]
    assert memory_guard.music_check(60, available_mb=118_000, total_mb=124_500, route="nonsense")["route"] == "music"


def tone(path: Path, seconds: float, parts, rate: int = 48000, channels: int = 2) -> None:
    """A WAV of sine parts: (frequency, amplitude, start, end)."""

    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            t = i / rate
            value = sum(a * math.sin(2 * math.pi * f * t) for f, a, s, e in parts if s <= t < e)
            frames += struct.pack("<h", int(max(-1, min(1, value)) * 32767)) * channels
        w.writeframes(bytes(frames))


def level(path: Path, start: float, end: float, filters: str) -> float:
    """RMS in dBFS of ``path``'s sound between two times, through ``filters``."""

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", str(path), "-af", filters,
                          "-ac", "1", "-f", "f32le", "pipe:1"], capture_output=True, timeout=60).stdout
    import numpy as np

    wave_ = np.frombuffer(raw, np.float32)
    return 20 * math.log10(float(np.sqrt(np.mean(wave_ ** 2))) + 1e-12)


def test_a_song_is_laid_under_a_clip_ducked_under_speech_and_never_clips():
    if not shutil.which("ffmpeg"):
        return
    folder = Path(tempfile.mkdtemp())
    sound, song = folder / "dialogue.wav", folder / "song.wav"
    # the clip: silence, then a 1 kHz "voice" from 2 to 4 s; the song: a loud 110 Hz line, longer than the clip
    tone(sound, 6, [(1000, 0.3, 2.0, 4.0)])
    tone(song, 9, [(110, 0.98, 0, 9)], rate=44100)
    clip = folder / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=128x72:r=24:d=6", "-i", str(sound),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True, timeout=120)
    out = folder / "laid.mp4"
    said = music.lay(clip, song, out, music_db=0.0, duck_db=12.0)
    assert said["ducked"] and said["peak_dbfs"] is not None and said["peak_dbfs"] <= music.PEAK_DBFS, said
    assert abs(server.media_seconds(str(out))[0] - 6.0) < 0.15
    low = "lowpass=f=300,lowpass=f=300"
    quiet, under = level(out, 0.5, 1.5, low), level(out, 2.6, 3.6, low)
    assert quiet - under >= 6.0, (quiet, under)  # the song is lowered while the voice speaks
    assert quiet - under <= 12.0 + 1.5, (quiet, under)  # by DUCK_DB at most
    voice_kept = level(out, 2.6, 3.6, "highpass=f=700")
    assert voice_kept > -20.0, voice_kept
    # a clip without sound: the song alone, limited all the same
    silent = folder / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=128x72:r=24:d=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(silent)], check=True, timeout=120)
    said = music.lay(silent, song, folder / "alone.mp4", music_db=0.0)
    assert not said["ducked"] and said["peak_dbfs"] <= music.PEAK_DBFS


def test_soundtrack_and_export_jobs_take_a_song():
    if not shutil.which("ffmpeg"):
        return
    folder = server.HOME / "music"
    folder.mkdir(parents=True, exist_ok=True)
    tone(folder / "song-t.wav", 4, [(220, 0.5, 0, 4)], rate=44100)
    (folder / "song-t.take.json").write_text(json.dumps({"id": "song-t", "path": "music/song-t.wav", "created": time.time(), "seed": 1,
                                                         "caption": "c", "lyrics": "[Instrumental]"}))
    clip = server.HOME / "renders" / "lay-clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=128x72:r=24:d=3", "-f", "lavfi", "-i",
                    "sine=f=1000:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True, timeout=120)
    assert client.post("/api/music/soundtrack", json={"take": "nope", "video": "renders/lay-clip.mp4"}).status_code in (400, 404)
    assert client.post("/api/music/soundtrack", json={"take": "song-t", "video": "renders/none.mp4"}).status_code == 400
    assert client.post("/api/music/soundtrack", json={"take": "song-t", "video": "renders/lay-clip.mp4", "music_db": 5}).status_code == 400
    job = client.post("/api/music/soundtrack", json={"take": "song-t", "video": "renders/lay-clip.mp4", "music_db": -6}).json()
    done = wait(job["id"], 120)
    assert done["status"] == "done", done
    assert done["outputs"][0].startswith("exports/") and done["soundtrack"]["peak_dbfs"] <= music.PEAK_DBFS
    # the Editor's export with a song under it
    bad = client.post("/api/jobs", json={"kind": "export", "params": {"clips": [{"path": "renders/lay-clip.mp4"}], "soundtrack": {"take": "nope"}}})
    assert bad.status_code in (400, 404)
    job = client.post("/api/jobs", json={"kind": "export", "params": {"clips": [{"path": "renders/lay-clip.mp4", "in": 0, "out": 0, "duration": 3}],
                                                                       "width": 320, "height": 180, "soundtrack": {"take": "song-t", "music_db": -8}}}).json()
    done = wait(job["id"], 180)
    assert done["status"] == "done", done
    assert done["soundtrack"]["ducked"] and done["soundtrack"]["peak_dbfs"] <= music.PEAK_DBFS
    assert done["params"]["soundtrack"]["song"] == "music/song-t.wav"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
