"""YuE2 as the Music tab's song engine beside MiniMax Music 3 (app/music_yue2.py, spark/yue2_server.py): python3 -m pytest
tests/test_music_yue2.py. No GPU, no model: YuE2's server is stood in for by a script that speaks its protocol."""
import importlib.util
import json
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import server  # noqa: E402
import music  # noqa: E402
import music_yue2  # noqa: E402
import settings  # noqa: E402
import voice  # noqa: E402
import yue2_server  # noqa: E402  (its request part needs no torch)
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
voice.LOCK = Path(os.environ["STUDIO_GPU_LOCK"])

# `python -I spark/yue2_server.py --model M --vae V --src S --quant Q --backend B --serve`, stood in for
YUE2_SERVE = r"""
import json, os, struct, sys, wave
args = sys.argv[1:]
assert args[0] == "-I" and args[1].endswith("yue2_server.py") and args[-1] == "--serve", args
with open(os.environ["FAKE_YUE2_LOG"], "a") as log:
    log.write(json.dumps({"argv": args}) + "\n")
print("[tensorfold] yue2 weights on the device: 9.3 GB", file=sys.stderr, flush=True)
quant = args[args.index("--quant") + 1]
print(json.dumps({"ready": True, "engine": "yue2-torch", "weights": quant, "backend": "torch", "load_s": 0.1,
                  "sample_rate": 48000, "channels": 2, "stream": False}), file=sys.stderr, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ["FAKE_YUE2_LOG"], "a") as log:
        log.write(line)
    if "FAIL" in request["style"]:  # the score was begun (its folder made), then the song failed
        os.makedirs(request["artifacts"], exist_ok=True)
        open(os.path.join(request["artifacts"], "score.abc"), "w").write("X:1\n")
        print(json.dumps({"id": request["id"], "ok": False, "error": "RuntimeError: the score came out empty"}), file=sys.stderr, flush=True)
        continue
    for stage, total in (("plan", 4096), ("semantic", 1501), ("nar", 64), ("vae", 2)):
        print("[tensorfold] yue2 %s step %d/%d" % (stage, total, total), file=sys.stderr, flush=True)
        print(json.dumps({"id": request["id"], "progress": {"stage": stage, "done": total, "total": total}}), file=sys.stderr, flush=True)
    with wave.open(request["wav"], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); w.writeframes(struct.pack("<hh", 100, -100) * 48000 * 2)
    os.makedirs(request["artifacts"], exist_ok=True)
    score = os.path.join(request["artifacts"], "score.abc")
    open(score, "w").write("X:1\nT:\nK:C\n")
    print(json.dumps({"id": request["id"], "ok": True, "audio_s": 2.0, "ended": False, "truncated": {"abc": False, "semantic": True},
                      "plan_ms": 5200, "semantic_ms": 18900, "nar_ms": 8500, "vae_ms": 1400, "total_ms": 34000, "rtf": 0.57,
                      "tokens": {"plan": 990, "semantic": 1501}, "peak_memory_gb": 15.3, "score": score, "cot": "full",
                      "instrumental": bool(request.get("instrumental")), "sample_rate": 48000, "weights": quant}), file=sys.stderr, flush=True)
"""


def install(folder: Path) -> Path:
    """A YuE2 install as spark/setup-yue2.sh leaves it, its server stood in for (YUE2_* name the parts)."""

    python = folder / "yue2" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!{sys.executable}\n" + YUE2_SERVE)
    python.chmod(0o755)
    for name in ("YuE2-3B/model.safetensors", "YuE2-3B/qwen.tiktoken", "YuE2-Vae/model.safetensors"):
        (folder / "yue2" / "weights" / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / "yue2" / "weights" / name).write_bytes(b"0")
    return folder / "yue2"


class Linux:
    """music.py on a Linux machine with an NVIDIA GPU, YuE2 installed under ``folder`` when ``installed``. The memory
    guard sees an idle DGX Spark, not the machine the tests run on (a CI runner has 16 GB)."""

    def __init__(self, folder: Path, installed: bool = True, **env):
        self.folder, self.installed = folder, installed
        self.env = {"YUE2_HOME": str(folder / "yue2"), "FAKE_YUE2_LOG": str(folder / "requests.jsonl"), "YUE2_GUARD_GB": "0", **env}

    def __enter__(self):
        import memory_guard

        self.had = (music.SPARK, music.PREFIX, music._engine, music_yue2._engine, music_yue2.nvidia,
                    {k: os.environ.get(k) for k in [*self.env, "STUDIO_MUSIC_ENGINE", "YUE2_QUANT", "YUE2_BACKEND"]})
        self.guard = memory_guard, memory_guard.meminfo, memory_guard.device_room_mb
        memory_guard.meminfo = lambda text=None: {"MemTotal": 124_500, "MemAvailable": 118_000, "MemFree": 118_000}
        memory_guard.device_room_mb = lambda: None
        if self.installed:
            install(self.folder)
        music.SPARK, music.PREFIX, music._engine, music_yue2._engine = True, self.folder / "prefix", None, None
        music_yue2.nvidia = lambda: True
        for key in ("STUDIO_MUSIC_ENGINE", "YUE2_QUANT", "YUE2_BACKEND"):
            os.environ.pop(key, None)
        os.environ.update(self.env)
        return self

    def __exit__(self, *_):
        for one in music.loaded_engines():
            one.release("test over")
        music.SPARK, music.PREFIX, music._engine, music_yue2._engine, music_yue2.nvidia, saved = self.had
        guard, guard.meminfo, guard.device_room_mb = self.guard
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def wait(job_id: str, timeout: float = 30) -> dict:
    began = time.time()
    while time.time() - began < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def requests_of(folder: Path) -> list[dict]:
    file = folder / "requests.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines()] if file.is_file() else []


@pytest.fixture(autouse=True)
def fresh_engines():
    """Each test makes its own song engines: one another test made (and the answers it keeps) is set aside and put
    back after."""

    had = music._engine, music_yue2._engine
    music._engine = music_yue2._engine = None
    yield
    music._engine, music_yue2._engine = had


# ---------- which engine ----------

def test_a_mac_makes_songs_with_minimax_music_3_and_yue2_says_why_not():
    had = music.SPARK, music.MAC
    try:
        music.SPARK, music.MAC = False, True
        assert music.default_engine() == "music3"
        assert music_yue2.Yue2().present() == "YuE2 has no Mac build: on a Mac the Music tab makes songs with MiniMax Music 3."
        assert not music_yue2.installed()
        state = music.engine_state("yue2")
        assert state["available"] is False and state["installed"] is False and "Mac" in state["why"]
        music.MAC = False  # neither a Mac nor Linux: the reason does not call it a Mac
        assert "Linux with an NVIDIA GPU" in music_yue2.Yue2().present() and "Mac" not in music_yue2.Yue2().present()
        # the shared engine asked on a Linux machine first does not keep that answer for the Mac
        music.SPARK, music.MAC, music_yue2._engine = True, False, None
        one = music_yue2.engine()
        assert "YuE2" in one.check() and "Mac" not in one.check()
        music.SPARK, music.MAC = False, True
        assert "no Mac build" in one.check() and "Mac" in music.engine_state("yue2")["why"]
    finally:
        music.SPARK, music.MAC = had


def test_yue2_chosen_where_it_is_not_installed_makes_songs_with_minimax_music_3(tmp_path):
    had = settings.HOME, music.SPARK, music.MAC
    try:
        settings.HOME = tmp_path
        settings.save({"presets": {"music_engine": "yue2"}})
        music.SPARK, music.MAC = False, True  # a Mac
        assert music.default_engine() == "music3" and music.state()["engine"] == "music3"
        os.environ["STUDIO_MUSIC_ENGINE"] = "yue2"
        assert music.default_engine() == "music3"
        os.environ.pop("STUDIO_MUSIC_ENGINE")
        # a song that names YuE2 on a Mac is refused with the reason, not with a missing engine
        with pytest.raises(HTTPException) as refused:
            music.checked({"caption": "Warm pop", "lyrics": "[Verse]\nla", "seconds": 30, "engine": "yue2"})
        assert refused.value.status_code == 400 and "no Mac build" in refused.value.detail
        with Linux(tmp_path / "machine", installed=False):  # a Spark without YuE2
            assert music.default_engine() == "music3"
        with Linux(tmp_path / "other"):
            assert music.default_engine() == "yue2"
    finally:
        os.environ.pop("STUDIO_MUSIC_ENGINE", None)
        settings.HOME, music.SPARK, music.MAC = had


def test_settings_offers_yue2_only_where_it_is_installed():
    from test_shell_page import run as page

    out = page("""answers["/api/settings"] = { settings: { presets: { music_engine: "yue2" } } }; await settingsLoad();
      const seg = () => { SET.section = "presets"; const v = viewSettings(); const s = find(v, n => n.attrs && n.attrs["aria-label"] === "Song engine" && n.attrs.role === "radiogroup");
        return s.kids.map(b => [text(b), b.attrs["aria-checked"]]); };
      S.music = { engines: { yue2: { installed: false, available: false }, music3: { available: true } } }; out.mac = seg();
      S.music.engines.yue2 = { installed: true, available: true }; out.spark = seg();""")
    assert out["mac"] == [["Best here", "true"], ["MiniMax Music 3", "false"]]
    assert out["spark"] == [["Best here", "false"], ["YuE2", "true"], ["MiniMax Music 3", "false"]]


def test_linux_with_yue2_installed_defaults_to_yue2_and_without_it_to_music_3():
    with Linux(Path(tempfile.mkdtemp())):
        assert music.default_engine() == "yue2" and isinstance(music.engine(), music_yue2.Yue2)
        assert music.engine("music3") is not music.engine("yue2")
    with Linux(Path(tempfile.mkdtemp()), installed=False):
        assert music.default_engine() == "music3" and "spark/setup-yue2.sh" in music_yue2.engine().present()
    with Linux(Path(tempfile.mkdtemp())):
        music_yue2.nvidia = lambda: False  # Linux with no NVIDIA GPU
        assert music.default_engine() == "music3" and "NVIDIA" in music_yue2.engine().present()


def test_the_setting_and_the_variable_choose_over_the_platform(tmp_path):
    had = settings.HOME
    try:
        settings.HOME = tmp_path
        with Linux(tmp_path / "machine"):
            settings.save({"presets": {"music_engine": "music3"}})
            assert music.default_engine() == "music3"
            settings.save({"presets": {"music_engine": "auto"}})
            assert music.default_engine() == "yue2"
            os.environ["STUDIO_MUSIC_ENGINE"] = "music3"
            assert music.default_engine() == "music3"
            os.environ.pop("STUDIO_MUSIC_ENGINE")
        # kept in settings.json, checked when set
        assert json.loads((tmp_path / "settings.json").read_text())["presets"]["music_engine"] == "auto"
        with pytest.raises(HTTPException):
            settings.save({"presets": {"music_engine": "suno"}})
        assert settings.DEFAULTS["presets"]["music_engine"] == "auto"
        assert settings.schema()["choices"]["presets.music_engine"] == ["auto", "yue2", "music3"]
    finally:
        settings.HOME = had


def test_settings_page_and_its_defaults_name_the_song_engine():
    page = (ROOT / "app/static/settings.js").read_text()
    assert 'music_engine: "auto"' in page and '"Song engine"' in page
    assert '{ yue2: "YuE2" }' in page and 'music3: "MiniMax Music 3"' in page


# ---------- the request ----------

def test_the_lyrics_are_read_as_yue2_reads_them():
    text, instrumental = music_yue2.lyrics_for("[Verse 2]\nOne two\n[Hook]\n(oh oh) three\n\n\n[Solo]\n[Post-Chorus]\nfour")
    assert text == "[Verse]\nOne two\n\n[Chorus]\n(oh oh) three\n\n[Interlude]\n\n[Chorus]\nfour" and instrumental is False
    assert music_yue2.native_tag("pre chorus") == "Pre-Chorus" and music_yue2.native_tag("Kazoo") == "Kazoo"
    assert music_yue2.lyrics_for("[Instrumental]") == ("[Instrumental]", True)
    assert music_yue2.lyrics_for("[Intro]\n[Verse]\n[Outro]")[1] is True
    assert music_yue2.lyrics_for("")[1] is True


def test_the_server_maps_length_style_and_an_instrumental():
    assert yue2_server.budget(60) == 1501 and yue2_server.budget(10) == 251 and yue2_server.budget(400) == 9000
    assert yue2_server.budget(5) == 201  # never under YuE2's own least (200 tokens and the end)
    style = yue2_server.instrumental_style("warm piano, no vocals.")
    assert style == "Instrumental, warm piano, no vocals, no singing, no choir, no spoken words."
    assert yue2_server.instrumental_style("Instrumental jazz trio").startswith("Instrumental jazz trio, no vocals")
    assert yue2_server.plan_lyrics("", 30) == "[Intro]\n\n[Verse]\n\n[Outro]\n"
    assert "[Bridge]" in yue2_server.plan_lyrics("", 240) and yue2_server.plan_lyrics("", 120).count("[") == 4 and yue2_server.plan_lyrics("", 70).count("[") == 3
    assert yue2_server.plan_lyrics("[Intro]\n[Instrumental]\n[Chorus]", 60) == "[Intro]\n\n[Chorus]\n"
    assert yue2_server.lyric_tags("X:1\n% intro\nV: Vocal\n% pre-chorus\n") == "[Intro]\n\n[Pre-Chorus]\n"


def test_the_fade_at_a_cut_length():
    np = pytest.importorskip("numpy")
    audio = np.ones((48000 * 3, 2), np.float32)
    yue2_server.fade_out(audio, 2.0)
    assert audio[0, 0] == 1 and audio[48000, 0] == 1 and abs(audio[-1, 1]) < 1e-6 and 0.4 < audio[48000 * 2, 0] < 0.6


def test_checked_takes_the_engine_and_refuses_what_is_wrong():
    with Linux(Path(tempfile.mkdtemp())):
        one = music_yue2.engine()
        one.known = (time.time(), "")
        params = music.checked({"caption": "  synth  pop ", "lyrics": "[Verse 1]\nla la", "seconds": 30, "seed": 3})
        assert params["engine"] == "yue2" and params["yue2_lyrics"] == "[Verse]\nla la" and params["instrumental"] is False
        assert "steps" not in params and "dit_cfg" not in params and params["seed"] == 3
        inst = music.checked({"caption": "piano", "lyrics": "", "seconds": 30, "engine": "yue2"})
        assert inst["lyrics"] == "[Instrumental]" and inst["instrumental"] is True and inst["yue2_lyrics"] == ""
        with pytest.raises(HTTPException) as bad:
            music.checked({"caption": "pop", "engine": "suno"})
        assert "yue2 or music3" in bad.value.detail
        with pytest.raises(HTTPException) as long:
            music.checked({"caption": "pop", "seconds": 400, "engine": "yue2"})
        assert "10 to 300" in long.value.detail
        assert music.guard_route({"engine": "yue2"}) == "music-yue2-int8"
        for body, said in (({"caption": "p" * (music.CAPTION_LIMIT + 1)}, "the style is at most"),
                           ({"caption": "pop", "lyrics": "l" * (music.LYRICS_LIMIT + 1)}, "the lyrics are at most")):
            with pytest.raises(HTTPException) as over:  # the tab's limits, the same for both engines
                music.checked({**body, "engine": "yue2"})
            assert said in over.value.detail
        one.known = (time.time(), "No YuE2 weights at /x")
        with pytest.raises(HTTPException) as missing:
            music.checked({"caption": "pop", "engine": "yue2"})
        assert "No YuE2 weights" in missing.value.detail


def test_yue2_runs_with_its_own_cuda_libraries_not_the_system_toolkit(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/own/lib")
    real = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: True if str(self).startswith("/usr/local/cuda") else real(self))
    assert music.Cuda().environment()["LD_LIBRARY_PATH"].startswith("/usr/local/cuda/lib64")  # MiniMax Music 3's engine
    env = music_yue2.Yue2().environment()
    assert env["LD_LIBRARY_PATH"] == "/opt/own/lib" and "/usr/local/cuda" not in env["LD_LIBRARY_PATH"]
    assert env["PATH"] == os.environ["PATH"]


def test_the_server_command_and_its_weights():
    with Linux(Path(tempfile.mkdtemp())) as here:
        argv = music_yue2.Yue2().argv()
        assert argv[1] == "-I" and argv[2].endswith("spark/yue2_server.py") and argv[-1] == "--serve"
        assert argv[argv.index("--quant") + 1] == "int8" and argv[argv.index("--backend") + 1] == "torch"
        assert argv[argv.index("--model") + 1] == str(here.folder / "yue2" / "weights" / "YuE2-3B")
        os.environ.update(YUE2_QUANT="bf16", YUE2_BACKEND="blocks")
        one = music_yue2.Yue2()
        assert one.weights == "bf16" and one.guard_route == "music-yue2-bf16" and one.RTF == one.RTF_OF["bf16"]
        assert one.argv()[one.argv().index("--backend") + 1] == "blocks"


def test_the_tokens_go_to_tensorfolds_engine_where_it_is_built():
    """With libtf_yue2.so built under PREFIX (spark/build-yue2-engine.sh, a GB10 only) the server runs with
    ``--backend blocks`` and the tab's estimates are the engine's; YUE2_BACKEND=torch keeps YuE2's own loop."""

    saved = os.environ.pop("TF_YUE2_LIB", None)
    try:
        with Linux(Path(tempfile.mkdtemp())) as here:
            plain = music_yue2.Yue2()
            assert plain.backend == "torch" and not music_yue2.engine_built()
            lib = here.folder / "prefix" / "tf-yue2" / "libtf_yue2.so"
            lib.parent.mkdir(parents=True)
            lib.write_bytes(b"")
            one = music_yue2.Yue2()
            assert music_yue2.engine_built() and one.backend == "blocks"
            assert one.argv()[one.argv().index("--backend") + 1] == "blocks" and one.RTF < plain.RTF
            assert one.estimate(60) < plain.estimate(60) and one.load_s < plain.load_s
            assert one.guard_route == "music-yue2-blocks" and one.NEED_GB > plain.NEED_GB
            os.environ["YUE2_BACKEND"] = "torch"
            mine = music_yue2.Yue2()
            assert mine.backend == "torch" and mine.estimate(60) == plain.estimate(60)
    finally:
        if saved is not None:
            os.environ["TF_YUE2_LIB"] = saved


def test_the_memory_guard_sizes_yue2_from_its_measured_line():
    import memory_guard

    for route, (fixed, per_second) in memory_guard.MUSIC_YUE2_ESTIMATE.items():
        said = memory_guard.music_check(60, available_mb=120_000, total_mb=124_000, route=route)
        assert said["route"] == route and said["need_mb"] == int(fixed + 60 * per_second) and said["fits"]
        for seconds, made, mb in memory_guard.MUSIC_YUE2_MEASURED[route]:  # the line is at or above every measured song
            assert fixed + per_second * seconds >= mb, (route, seconds, mb)
    assert not memory_guard.music_check(300, available_mb=20_000, total_mb=124_000, route="music-yue2-int8")["fits"]


# ---------- a song, end to end ----------

def test_a_yue2_song_runs_on_its_server_holds_the_lock_and_gives_way(tmp_path):
    had_ready = music.qa.ready
    with Linux(tmp_path) as here:
        one = music_yue2.engine()
        one.NEED_GB, one.known = 0, (time.time(), "")
        one.comfy_holds = lambda: ""
        try:
            music.qa.ready = lambda: {"transcript": False, "runs": True, "missing": []}
            state = client.get("/api/music/state").json()
            assert state["engine"] == "yue2" and state["route"] == "yue2" and state["credit"] == "YuE2"
            assert "CC BY-NC 4.0" in state["licence"] and "royalty-free" in state["licence"] and "company" in state["licence"]
            assert set(state["engines"]) == {"yue2", "music3"} and state["engines"]["music3"]["credit"] == "MiniMax-Music3"
            assert state["engines"]["yue2"]["installed"] is True and state["rate"] == 48000
            job = client.post("/api/music/takes", json={"caption": "Indie pop, 112 BPM", "lyrics": "[Verse 2]\nMorning light", "seconds": 60, "seed": 7}).json()
            assert job["kind"] == "music" and job["params"]["engine"] == "yue2" and job["estimate"] >= int(60 * one.RTF)
            done = wait(job["id"])
            assert done["status"] == "done", (done.get("error"), done.get("log", "")[-400:])
            take = done["music"]
            assert take["engine"] == "yue2" and take["route"] == "yue2" and take["credit"] == "YuE2" and take["duration"] == 2.0
            assert take["ended"] is False and take["truncated"]["semantic"] is True and take["plan_s"] == 5.2 and take["nar_s"] == 8.5
            assert take["weights"] == "int8" and take["peak_memory_gb"] == 15.3 and take["score"].endswith(".yue2/score.abc")
            assert (server.HOME / take["score"]).is_file() and (server.HOME / take["path"]).is_file()
            assert "steps" not in take and take["instrumental"] is False
            audio = client.get(f"/api/music/takes/{job['id']}/audio")
            assert audio.status_code == 200 and "yue2-" in audio.headers["content-disposition"]
            sent = requests_of(here.folder)[-1]
            assert sent["style"] == "Indie pop, 112 BPM" and sent["lyrics"] == "[Verse]\nMorning light" and sent["seconds"] == 60.0
            assert sent["seed"] == 7 and sent["instrumental"] is False and sent["progress"] is True and sent["cot"] == "full"
            # resident: loaded after the song, under the GPU lock; a second song does not load it again
            assert one.loaded() and voice.lock_owner().startswith("studio-music yue2") and music.room_mb() == int(one.before_gb * 1024)
            pid = one.process.pid
            again = wait(client.post("/api/music/takes", json={"caption": "piano", "seconds": 20, "engine": "yue2"}).json()["id"])
            assert again["status"] == "done" and one.process.pid == pid and again["music"]["load_s"] is None
            assert again["music"]["instrumental"] is True and requests_of(here.folder)[-1]["instrumental"] is True
            failed = wait(client.post("/api/music/takes", json={"caption": "FAIL", "seconds": 10}).json()["id"])
            assert failed["status"] == "failed" and "score came out empty" in failed["error"] and one.loaded()
            assert not (music.HOME / "music" / f"{failed['id']}.yue2").exists()  # its half-made score went with it
            assert (music.HOME / "music" / f"{job['id']}.yue2" / "score.abc").is_file()  # a finished song's stays
            # the takes and the Library list it as a YuE2 song
            listed = {t["id"]: t for t in client.get("/api/music/takes").json()}
            assert listed[job["id"]]["engine"] == "yue2" and again["id"] in listed and failed["id"] not in listed
            # any other job of this machine's queue unloads it, and the lock goes back
            assert music.release("an image job needs the GPU") is True and not one.loaded() and voice.lock_owner() == ""
            assert music.release("again") is False
        finally:
            music.qa.ready = had_ready


def test_a_song_on_one_engine_unloads_the_other_first(tmp_path):
    with Linux(tmp_path):
        one = music_yue2.engine()
        one.NEED_GB, one.known = 0, (time.time(), "")
        one.comfy_holds = lambda: ""
        released = []

        class Other:
            name = "cuda"

            def release(self, why):
                released.append(why)
                return False

            def loaded(self):
                return False

            def check(self):
                return ""

        music._engine = Other()
        had_ready = music.qa.ready
        try:
            music.qa.ready = lambda: {"transcript": False, "runs": True, "missing": []}
            done = wait(client.post("/api/music/takes", json={"caption": "pop", "lyrics": "[Verse]\nla", "seconds": 10, "engine": "yue2"}).json()["id"])
            assert done["status"] == "done" and released == ["a song on YuE2 needs the GPU"]
        finally:
            music.qa.ready = had_ready
            music._engine = None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="local GPU RAM admission uses Linux process identity")
def test_local_ram_admits_the_requested_song_engine_not_the_default(tmp_path, monkeypatch):
    import local_gpus

    with Linux(tmp_path, MUSIC3_BACKEND="comfy", YUE2_QUANT="int8"):
        monkeypatch.setenv("STUDIO_MUSIC_ENGINE", "music3")
        monkeypatch.setattr(local_gpus, "memory", lambda: (100000, 42000))
        chosen = {"kind": "music", "params": {"engine": "yue2", "seconds": 60}, "status": "running"}
        with local_gpus.ram_reservation(server.local_ram_need(chosen), chosen) as admitted:
            assert admitted
        other = {"kind": "music", "params": {"engine": "music3", "seconds": 60}, "status": "running"}
        with pytest.raises(RuntimeError, match="RAM"):
            with local_gpus.ram_reservation(server.local_ram_need(other), other):
                pytest.fail("the larger MiniMax song cannot fit this host's remaining RAM")


# ---------- the MCP tool ----------

def test_the_mcp_song_tool_takes_an_engine(monkeypatch):
    spec = importlib.util.spec_from_file_location("studio_mcp_yue2", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp)
    sent = []
    monkeypatch.setattr(mcp, "call", lambda method, path, body=None, **_: sent.append((method, path, body)) or
                        {"id": "j1", "kind": "music", "status": "queued", "outputs": [], "params": body})
    tool = next(t for t in mcp.TOOLS if t["name"] == "queue_song")
    assert tool["inputSchema"]["properties"]["engine"]["enum"] == ["yue2", "music3"]
    reply = mcp.answer({"method": "tools/call", "params": {"name": "queue_song", "arguments": {"caption": "pop", "engine": "yue2", "seconds": 30}}})
    assert not reply.get("isError"), reply
    assert sent[-1] == ("POST", "/api/music/takes", {"caption": "pop", "seconds": 30, "engine": "yue2"})
    mcp.answer({"method": "tools/call", "params": {"name": "queue_song", "arguments": {"caption": "pop"}}})
    assert "engine" not in sent[-1][2]


def test_the_server_script_has_a_seam_for_another_decoder_block_engine():
    text = (ROOT / "spark/yue2_server.py").read_text()
    assert '"blocks"' in text and "tensorfold_yue2" in text and "prepare(model" in text


def test_a_refusal_for_memory_says_both_figures_to_a_tenth_of_a_gigabyte(monkeypatch):
    """27.56 GB free against a need of 28 read "needs about 28 GB ... has 28 GB" when both were rounded to whole ones."""

    one = music_yue2.Yue2()
    monkeypatch.setattr(one, "present", lambda: "")
    monkeypatch.setattr(voice, "mem_available_gb", lambda: 27.56)
    monkeypatch.setattr(voice, "comfy_up", lambda port=None: False)
    monkeypatch.setattr(voice, "has_card_memory", lambda: False)
    one.NEED_GB = 28.0
    said = one.check()
    assert "needs about 28.0 GB" in said and "has 27.6 GB available" in said


def test_the_server_gives_back_what_a_failed_song_held():
    text = (ROOT / "spark" / "yue2_server.py").read_text()
    failing = text[text.index("answer = engine.song(request, progress)"):]
    assert failing.index("free_cache()") < failing.index('say({"id": job, **answer})')
    assert "torch.cuda.empty_cache()" in text[text.index("def free_cache"):text.index("def read_weights")]
    yue2_server.free_cache()  # with no GPU (or no torch) it does nothing and says nothing
