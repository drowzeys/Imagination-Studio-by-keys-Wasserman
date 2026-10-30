"""Voice, Fish Audio Drama 3 (app/voice.py): python3 tests/test_voice.py. No GPU, no model, no network: the engine's
command line is stood in for by a small script that writes a WAV and its stats."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ["STUDIO_GPU_LOCK"] = str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d")
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import slate  # noqa: E402
import voice  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
voice.LOCK = Path(os.environ["STUDIO_GPU_LOCK"])

FAKE = r'''
import json, sys, wave, struct
args = sys.argv[1:]
out, stats = args[args.index("--out") + 1], args[args.index("--stats") + 1]
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(struct.pack("<h", 0) * 44100 * 3)
json.dump({"frames": 65, "audio_s": 3.0, "decode_s": 1.0, "frames_per_s": 63.0, "rtf": 0.4, "total_s": 1.2,
           "peak_memory_gb": 48.0, "chunks": 1, "finish": ["stop"]}, open(stats, "w"))
print("[drama3] wrote", out, "seed", args[args.index("--seed") + 1], "lock", __import__("os").path.isdir(sys.argv[0] + ".lock"))
'''


class Fake:
    name, label = "mlx", "stand-in"

    def __init__(self, folder: Path):
        self.script = folder / "fake_drama3.py"
        self.script.write_text(FAKE)
        self.seen = []

    def check(self) -> str:
        return ""

    def command(self, text_file, out, stats, reference, seed, sampling):
        self.seen.append({"text": text_file.read_text(), "reference": reference, "seed": seed, "sampling": sampling,
                          "locked": voice.LOCK.is_dir()})
        return [sys.executable, str(self.script), "--out", str(out), "--stats", str(stats), "--seed", str(seed)]


def wait(job_id: str, timeout: float = 30) -> dict:
    started = time.time()
    while time.time() - started < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def test_a_sheet_becomes_a_direction_and_its_sample_line():
    sheet = {"name": "Old Tom", "ageGender": "a man in his late fifties", "accent": "a trace of Lisbon", "timbre": "gravelly",
             "pitch": "low", "pacing": "slow, patient", "energy": "", "texture": "smoke [and gravel]", "sampleLine": "Sit down.  Tell me."}
    text = voice.sheet_text(sheet)
    assert text == "[the voice of a man in his late fifties, a trace of Lisbon, gravelly, low, slow, patient, smoke (and gravel)] Sit down. Tell me."
    assert voice.spoken(text) == "Sit down. Tell me."
    assert voice.sheet_text({}) == voice.SAMPLE_LINE  # no description: the line alone, never an empty direction


def test_the_word_check_leaves_out_tags_and_directions():
    got = voice.match("<|speaker:0|> [whispering] Did you hear that? <|speaker:1|> No.", "Did you hear that? No.")
    assert got["score"] == 100 and got["found"] == 5 and got["words"] == 5
    assert voice.match("one two three four", "one two")["score"] < 100


SERVE = r"""
import json, os, struct, sys, time, wave
args = sys.argv[1:]
assert args[args.index("--serve") + 1] == "1" and "--model" in args and "--codec" in args
log = open(os.environ["FAKE_DRAMA3_LOG"], "a")
print("info: drama3: weights on the device: 38.58 GB", file=sys.stderr, flush=True)
print("not json {", file=sys.stderr, flush=True)
print(json.dumps({"ready": True}), file=sys.stderr, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    log.write(line); log.flush()
    if "FAIL" in request["text"]:
        print(json.dumps({"ok": False, "error": "TextTooLong"}), file=sys.stderr, flush=True)
        continue
    if "CRASH" in request["text"]:
        sys.exit(3)
    samples = struct.pack("<h", 100) * 44100
    if "STALL" in request["text"] and (request["seed"] % 2 == 0 or "ALWAYS" in request["text"]):
        samples = struct.pack("<hh", 8000, -8000) * 44100 + struct.pack("<h", 300) * 44100 * 4  # 2 s spoken, then 4 s of hum
    if request.get("pcm"):
        with open(request["pcm"], "wb") as pcm:
            for i in range(4):
                pcm.write(samples[i * 22050:(i + 1) * 22050]); pcm.flush(); time.sleep(0.05)
    with wave.open(request["wav"], "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(samples)
    print("info: drama3: a log line between answers", file=sys.stderr, flush=True)
    print(json.dumps({"ok": True, "frames": 21, "eos": True, "audio_s": 1.0, "prompt_rows": 30, "ref_frames": 40 if request.get("ref_audio") else 0,
                      "encode_ms": 100.0 if request.get("ref_audio") else 0.0, "prefill_ms": 110.0, "first_audio_ms": 250.0,
                      "total_ms": 700.0, "fps": 30.0, "wav": request["wav"]}), file=sys.stderr, flush=True)
"""


def fake_cuda(folder: Path) -> "voice.Cuda":
    """A Cuda backend whose engine is a stand-in speaking the same protocol (--serve 1, JSON lines)."""

    engine = folder / "bin" / "drama3-generate"
    engine.parent.mkdir(parents=True)
    engine.write_text(f"#!{sys.executable}\n" + SERVE)
    engine.chmod(0o755)
    (folder / "lib").mkdir()
    (folder / "lib" / "libtensorfold_drama3.so").write_bytes(b"")
    model = folder / "drama-3"
    model.mkdir()
    (model / "config.json").write_text("{}")
    (model / "model.safetensors.index.json").write_text("{}")
    (folder / "codec-v1.safetensors").write_bytes(b"")
    os.environ.update(DRAMA3_CUDA_BIN=str(engine), DRAMA3_CUDA_MODEL=str(model), DRAMA3_CUDA_CODEC=str(folder / "codec-v1.safetensors"),
                      FAKE_DRAMA3_LOG=str(folder / "requests.jsonl"))
    try:
        one = voice.Cuda()
    finally:
        for key in ("DRAMA3_CUDA_BIN", "DRAMA3_CUDA_MODEL", "DRAMA3_CUDA_CODEC"):
            os.environ.pop(key)
    one.NEED_GB = 0  # the machine's memory is not this test's business
    return one


def requests_of(folder: Path) -> list[dict]:
    file = folder / "requests.jsonl"
    return [json.loads(line) for line in file.read_text().splitlines()] if file.is_file() else []


def test_the_cuda_backend_says_what_is_missing():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    assert one.check() == ""
    assert one.argv() == [str(one.bin), "--model", str(one.model), "--codec", str(one.codec), "--serve", "1"]
    (folder / "codec-v1.safetensors").unlink()
    one.known = None
    had_located = voice.located
    voice.located = lambda need, path, there: path  # this desk's linked weights are not the missing install
    had = voice._backend
    try:
        assert "drama3_codec.py" in one.check() and "oneshot-setup-spark.sh --voice" in one.check() and "DRAMA3_CUDA_CODEC" in one.check()
        one.known, one.model = None, folder / "nowhere"
        assert "No Drama 3 checkpoint" in one.check()
        one.known, one.bin = None, folder / "nothing"
        why = one.check()
        assert "drama3-cuda" in why and "DRAMA3_CUDA_BIN" in why
        voice._backend = one
        state = client.get("/api/state").json()["voice"]
        assert state["available"] is False and state["backend"] == "cuda" and state["credit"] == "Built with Fish Audio"
        assert state["server"]["loaded"] is False and state["live"] is True
        answer = client.post("/api/voice/takes", json={"text": "Hello."})
        assert answer.status_code == 400 and "DRAMA3_CUDA_BIN" in answer.json()["error"]
    finally:
        voice.located = had_located
        voice._backend = had


def test_long_text_goes_in_parts_at_sentence_ends_with_its_speaker():
    short = "<|speaker:0|> Hello there. <|speaker:1|> Hi."
    assert voice.pieces(short, 60) == [short]
    text = "<|speaker:0|> One two three. Four five six. <|speaker:1|> [sighs] Seven eight nine ten. Eleven."
    parts = voice.pieces(text, 6)
    assert parts == ["<|speaker:0|> One two three. Four five six.", "<|speaker:1|> [sighs] Seven eight nine ten. Eleven."], parts
    parts = voice.pieces("<|speaker:1|> " + " ".join(f"Line {n} is here." for n in range(10)), 8)
    assert len(parts) == 5 and all(part.startswith("<|speaker:1|> Line") for part in parts)  # the speaker is said again
    assert voice.pieces("No tags. " * 30, 20)[1].startswith("No tags.")
    assert voice.one_speaker("<|speaker:0|> a <|speaker:0|> b") and voice.one_speaker("plain") and not voice.one_speaker("<|speaker:2|> a")


def test_a_cuda_take_runs_on_a_persistent_server_that_holds_the_lock_until_unloaded():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    had = voice._backend
    try:
        voice._backend = one
        job = client.post("/api/voice/takes", json={"text": "Hello there.", "seed": 5, "top_k": 25, "asr": False}).json()
        done = wait(job["id"])
        assert done["status"] == "done", done
        take = done["voice"]
        assert take["backend"] == "cuda" and take["duration"] == 1.0 and take["frames"] == 21 and take["frames_per_s"] == 30.0
        assert take["first_audio_ms"] == 250.0 and take["load_s"] is not None and take["device_gb"] == 38.58 and take["chunks"] == 1
        sent = requests_of(folder)[-1]
        assert sent["text"] == "Hello there." and sent["seed"] == 5 and sent["top_k"] == 25 and sent["temperature"] == 1.0
        assert sent["top_p"] == 0.9 and sent["max_frames"] == 1500 and sent["wav"].endswith(f"{job['id']}.wav") and "ref_audio" not in sent
        assert (server.HOME / take["path"]).is_file()
        # loaded after the take, holding the GPU lock: the next take does not load again
        assert one.loaded() and voice.lock_owner().startswith("studio-voice drama3")
        assert client.get("/api/voice/state").json()["server"]["loaded"] is True
        pid = one.process.pid
        job = client.post("/api/voice/takes", json={"text": "<|speaker:0|> Again. <|speaker:1|> Yes.", "asr": False}).json()
        take = wait(job["id"])["voice"]
        assert one.process.pid == pid and take["load_s"] is None and take["streamed"] is True
        assert requests_of(folder)[-1]["text"] == "<|speaker:0|> Again.\n<|speaker:1|> Yes."
        # a reference clip is sent as WAV with its text
        clip = server.HOME / "uploads" / "cuda-ref.wav"
        with wave.open(str(clip), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(22050); w.writeframes(struct.pack("<hh", 0, 0) * 22050 * 3)
        job = client.post("/api/voice/takes", json={"text": "Cloned.", "ref_audio": "uploads/cuda-ref.wav", "ref_text": "the words", "consent": True, "asr": False}).json()
        assert wait(job["id"])["status"] == "done"
        sent = requests_of(folder)[-1]
        assert sent["ref_text"] == "the words" and sent["ref_audio"].endswith(".ref.wav") and Path(sent["ref_audio"]).is_file()
        if voice.tool("ffmpeg"):
            with wave.open(sent["ref_audio"]) as w:
                assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 44100, 2)
        # an engine error fails the take and keeps the server
        job = client.post("/api/voice/takes", json={"text": "Please FAIL.", "asr": False}).json()
        failed = wait(job["id"])
        assert failed["status"] == "failed" and "TextTooLong" in failed["error"] and one.loaded()
        # any other job of this machine's queue unloads it first, and the lock goes back
        assert voice.release("a video job needs the GPU") is True
        assert not one.loaded() and not voice.LOCK.exists() and one.events[-1]["why"] == "a video job needs the GPU"
        assert voice.release("again") is False
        # an engine that dies mid-take fails the take and gives the lock back
        job = client.post("/api/voice/takes", json={"text": "CRASH now.", "asr": False}).json()
        failed = wait(job["id"])
        assert failed["status"] == "failed" and "stopped" in failed["error"], failed
        assert not one.loaded() and not voice.LOCK.exists()
    finally:
        one.release("test over")
        voice._backend = had


def test_a_long_cuda_take_keeps_one_voice_plays_while_made_and_unloads_when_idle():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    one.CHUNK_WORDS, one.idle_s = 4, 1
    had = voice._backend
    try:
        voice._backend = one
        job = client.post("/api/voice/takes", json={"text": "One two three. Four five six. Seven eight nine.", "seed": 9, "asr": False}).json()
        live = client.get(f"/api/voice/takes/{job['id']}/live")
        assert live.status_code == 200 and live.headers["content-type"] == "audio/wav"
        body = live.content
        assert body[:4] == b"RIFF" and body[8:16] == b"WAVEfmt " and body[36:40] == b"data"
        gap = int(44100 * one.GAP_S) * 2
        assert len(body) == 44 + 3 * 88200 + 2 * gap, len(body)
        take = wait(job["id"])["voice"]
        assert take["chunks"] == 3 and abs(take["duration"] - (3 + 2 * one.GAP_S)) < 1e-6
        with wave.open(str(server.HOME / take["path"])) as w:
            assert w.getnframes() == 3 * 44100 + 2 * int(44100 * one.GAP_S)
        sent = requests_of(folder)[-3:]
        assert [r["seed"] for r in sent] == [9, 10, 11] and "ref_audio" not in sent[0]
        assert sent[1]["ref_audio"] == sent[2]["ref_audio"] == sent[0]["wav"] and sent[1]["ref_text"] == "One two three."
        assert not list((server.HOME / "voice").glob(f"{job['id']}.part*.wav"))
        # five idle polls at most: the idle thread unloads it and gives the lock back
        started = time.time()
        while (one.loaded() or voice.LOCK.exists()) and time.time() - started < 20:
            time.sleep(0.2)
        assert not one.loaded() and not voice.LOCK.exists() and "idle" in one.events[-1]["why"]
    finally:
        one.release("test over")
        voice._backend = had


def test_an_h3_job_unloads_drama3_before_it_runs():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    had, built = voice._backend, server.build
    try:
        voice._backend = one
        assert wait(client.post("/api/voice/takes", json={"text": "Loaded.", "asr": False}).json()["id"])["status"] == "done"
        assert one.loaded() and voice.LOCK.exists()
        seen = {}

        def build(kind, params, job_id, room=True):  # a stand-in render: what it sees of the lock and the server
            seen.update(loaded=one.loaded(), locked=voice.LOCK.exists())
            out = server.HOME / "renders" / f"{job_id}.png"
            return [sys.executable, "-c", f"open({str(out)!r}, 'wb').write(b'png')"], {}, [str(out)]

        # while it is loaded a clip is measured against the memory the machine had before it loaded
        assert voice.room_mb() == int(one.before_gb * 1024)
        if server.SPARK:
            had_room, had_device = server.memory_guard.room_mb, server.memory_guard.device_room_mb
            try:
                server.memory_guard.room_mb = lambda: 1000
                server.memory_guard.device_room_mb = lambda: None  # a card reads its own room and would ignore the stand-in
                assert server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 121, False)["available_mb"] == voice.room_mb()
            finally:
                server.memory_guard.room_mb = had_room
                server.memory_guard.device_room_mb = had_device
        server.build = build
        job = {"id": "test-h3-yield", "kind": "image", "status": "running", "title": "t", "params": {}, "log": "", "outputs": [],
               "started": time.time(), "finished": None, "remote": None}
        server.perform(job)
        assert job["status"] == "done", job
        assert seen == {"loaded": False, "locked": False} and "Drama 3 was unloaded" in job["log"]
        assert voice.room_mb() is None
    finally:
        server.build = built
        one.release("test over")
        voice._backend = had


def test_a_take_is_checked_before_it_is_queued():
    had = voice._backend
    try:
        voice._backend = Fake(Path(tempfile.mkdtemp()))
        for body, says in (({"text": "  [sighs] "}, "no words"), ({"text": "<|speaker:16|> Hi"}, "speaker"),
                           ({"text": "Hi", "temperature": 3}, "temperature"), ({"text": "x" * (voice.TEXT_LIMIT + 1)}, "characters")):
            answer = client.post("/api/voice/takes", json=body)
            assert answer.status_code == 400 and says in answer.json()["error"], (body, answer.json())
        clip = server.HOME / "uploads" / "voice-ref.wav"
        with wave.open(str(clip), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(struct.pack("<h", 0) * 16000 * 4)
        answer = client.post("/api/voice/takes", json={"text": "Hi there.", "ref_audio": "uploads/voice-ref.wav", "ref_text": "hello"})
        assert answer.status_code == 400 and "consent" in answer.json()["error"]
    finally:
        voice._backend = had


def test_a_take_runs_under_the_gpu_lock_and_is_listed():
    had = voice._backend
    fake = Fake(Path(tempfile.mkdtemp()))
    try:
        voice._backend = fake
        job = client.post("/api/voice/takes", json={"text": "<|speaker:0|> Hello there. <|speaker:1|> Hi.", "seed": 7, "top_k": 20, "asr": False}).json()
        assert job["kind"] == "voice" and job["status"] in ("queued", "running")
        done = wait(job["id"])
        assert done["status"] == "done", done
        assert fake.seen[-1]["locked"] is False  # the command is built before the lock is taken ...
        assert not voice.LOCK.exists()  # ... and the lock is given back after the run
        assert fake.seen[-1]["seed"] == 7 and fake.seen[-1]["sampling"] == {"temperature": 1.0, "top_p": 0.9, "top_k": 20}
        take = done["voice"]
        assert take["duration"] == 3.0 and take["frames_per_s"] == 63.0 and take["path"] == f"voice/{job['id']}.wav"
        listed = client.get("/api/voice/takes").json()
        assert listed[0]["id"] == job["id"]
        audio = client.get(f"/api/voice/takes/{job['id']}/audio")
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
        # a lock held by someone else: the take waits, and a cancelled take stops waiting
        voice.LOCK.mkdir()
        (voice.LOCK / "owner").write_text(f"trackc test {os.getpid()}\n")
        job = client.post("/api/voice/takes", json={"text": "Waiting.", "asr": False}).json()
        time.sleep(0.5)
        assert "GPU lock" in (client.get(f"/api/jobs/{job['id']}").json()["progress"] or {}).get("stage", "")
        client.post(f"/api/jobs/{job['id']}/cancel", json={})
        assert wait(job["id"])["status"] == "cancelled" and (voice.LOCK / "owner").read_text().startswith("trackc")
        # a lock whose owner is gone is stale and taken
        (voice.LOCK / "owner").write_text("trackc test 999999999\n")
        assert wait(client.post("/api/voice/takes", json={"text": "Stale.", "asr": False}).json()["id"])["status"] == "done"
        assert not voice.LOCK.exists()
    finally:
        voice._backend = had
        if voice.LOCK.exists():
            import shutil
            shutil.rmtree(voice.LOCK)


def test_a_cast_voice_goes_into_a_shot_on_cast_stills_as_reference_audio():
    home = slate.HOME
    for name in ("leo.png", "mia.png"):
        (home / "uploads" / name).write_bytes(b"png")
    (home / "voice").mkdir(exist_ok=True)
    (home / "voice" / "tom.wav").write_bytes(b"wav")
    (home / "voice" / "long.wav").write_bytes(b"wav")
    project = {"id": "p-v", "name": "V", "defaults": {"durationSec": 8}, "tf": {"res": "480p", "quality": "fh8", "dialogue": "drama3"},
               "characters": [{"id": "c1", "name": "Leo", "images": ["uploads/leo.png"]}, {"id": "c2", "name": "Mia", "images": ["uploads/mia.png"]}],
               "voices": [{"id": "v1", "name": "Leo voice", "characterId": "c1", "ageGender": "a young man", "timbre": "warm",
                           "drama": {"path": "voice/tom.wav", "seconds": 6.2, "use": True}},
                          {"id": "v2", "name": "Mia voice", "characterId": "c2", "ageGender": "a young woman", "drama": {"path": "voice/long.wav", "seconds": 18.0}}],
               "scenes": [{"id": "s1", "name": "S", "shots": [{"id": "sh", "name": "1", "spec": {"durationSec": 8}, "prompt": "# Subject\nThey talk.", "takes": [],
                                                               "tf": {"start": "cast", "cast": ["c1", "c2"], "lines": [{"characterId": "c2", "text": "Hi Leo."}, {"characterId": "c1", "text": "Hello."}]}}]}]}
    had = slate.ready
    try:
        slate.ready = lambda: {"ref2va": True, "ref2va_turbo": False}
        made = slate.compile_shot(project, "sh")
        assert made["plan"]["reference_audios"] == ["voice/tom.wav"]  # Mia's 18 s take is left out, and said
        assert any("18.0 s" in w for w in made["warnings"])
        assert "<Audio 1> is the voice timbre reference for <Subject 1>'s voice, containing the voice of a young man (warm)." in made["prompt"]
        assert "references the delivery from <Audio 1>" in made["prompt"]
        project["voices"][0]["drama"]["use"] = False  # unticked: no reference audio
        assert "reference_audios" not in slate.compile_shot(project, "sh")["plan"]
        project["scenes"][0]["shots"][0]["tf"]["start"] = "text"  # not on reference pictures (the bible off): never audio alone
        project["tf"]["bible"] = "off"
        project["voices"][0]["drama"]["use"] = True
        assert "reference_audios" not in slate.compile_shot(project, "sh")["plan"]
        project["tf"]["bible"] = "auto"  # from text, the bible's pictures are references: the cast voice goes in
        assert slate.compile_shot(project, "sh")["plan"]["reference_audios"] == ["voice/tom.wav"]
        project["tf"]["dialogue"] = "h3"  # dialogue spoken by the video model: no voice reference
        assert "reference_audios" not in slate.compile_shot(project, "sh")["plan"]
        # a film made before the settings: shots on cast stills take the cast voices, as they did
        del project["tf"]["dialogue"], project["tf"]["bible"]
        project["scenes"][0]["shots"][0]["tf"]["start"] = "cast"
        assert slate.compile_shot(project, "sh")["plan"]["reference_audios"] == ["voice/tom.wav"]
    finally:
        slate.ready = had


STREAMING = r"""
import json, struct, sys, time
args = sys.argv[1:]
out, stats = args[args.index("--out") + 1], args[args.index("--stats") + 1]
print("[drama3] loaded /fake/drama-3-mlx in 3.7s", flush=True)
samples = struct.pack("<h", 7) * 44100 * 2
with open(out, "wb") as w:
    w.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16) + b"data" + struct.pack("<I", 0xFFFFFFFF - 36))
    w.flush()
    for i in range(8):
        w.write(samples[i * 22050:(i + 1) * 22050]); w.flush(); time.sleep(0.05 if "--stream" in args else 0)
    w.seek(4); w.write(struct.pack("<I", 36 + len(samples))); w.seek(40); w.write(struct.pack("<I", len(samples)))
short = "--stream" in args and "--short" in args
json.dump({"frames": 43, "audio_s": 2.0, "frames_per_s": 73.0, "rtf": 0.35, "total_s": 0.7, "first_audio_s": 0.234, "stream": "--stream" in args,
           "peak_memory_gb": 49.4, "chunks": 2, "finish": ["eos", "stop_rule"],
           "verify": [{"words": 5, "matched": 5, "missing": [], "action": "ok", "attempts": 1, "heard": "One two three four five.", "asr_s": 0.1, "coverage": 1.0},
                      {"words": 4, "matched": 3, "missing": ["cat"], "action": "redraw" if short else "trim", "attempts": 2, "cut_s": 1.9, "heard": "It is the", "asr_s": 0.1}]},
          open(stats, "w"))
print("[drama3] wrote", out, "first audio 0.234s, total 0.7s", flush=True)
"""


class FakeStreaming(Fake):
    """The MLX engine from drama3-mlx 4a8a982f on: --stream writes a growing WAV, its stats carry its own check."""

    streams = True

    def __init__(self, folder: Path):
        super().__init__(folder)
        self.script.write_text(STREAMING)

    def command(self, text_file, out, stats, reference, seed, sampling, live=False):
        command = super().command(text_file, out, stats, reference, seed, sampling)
        self.seen[-1]["live"] = live
        return command + (["--stream"] if live else []) + (["--short"] if "SHORT" in self.seen[-1]["text"] else [])


def test_an_mlx_take_plays_while_made_and_its_score_is_the_engines_check():
    had, had_ready, had_transcribe = voice._backend, voice.qa.ready, voice.qa.transcribe
    fake = FakeStreaming(Path(tempfile.mkdtemp()))
    try:
        voice._backend = fake
        voice.qa.ready = lambda: {"transcript": True, "runs": True, "missing": []}
        voice.qa.transcribe = lambda clip: (_ for _ in ()).throw(AssertionError("transcribed twice"))
        state = client.get("/api/voice/state").json()
        assert state["live"] is True and state["checks"] is True
        job = client.post("/api/voice/takes", json={"text": "One two three four five. It is the cat.", "seed": 3}).json()
        body = client.get(f"/api/voice/takes/{job['id']}/live").content
        assert body[:4] == b"RIFF" and len(body) == 44 + 2 * 88200, len(body)  # the engine's own header is not sent twice
        done = wait(job["id"])
        assert done["status"] == "done", done
        take = done["voice"]
        assert fake.seen[-1]["live"] is True and take["streamed"] is True
        assert take["first_audio_ms"] == 234.0 and take["load_s"] == 3.7 and take["frames_per_s"] == 73.0
        assert [v["action"] for v in take["verify"]] == ["ok", "trim"] and take["verify"][1]["missing"] == ["cat"]
        assert take["asr"]["by"] == "engine" and take["asr"]["found"] == 8 and take["asr"]["words"] == 9 and take["asr"]["score"] == 89
        assert take["asr"]["missing"] == ["cat"]
        with wave.open(str(server.HOME / take["path"])) as w:
            assert w.getnframes() == 2 * 44100
        # a part short while it played: made once more without playing, where the engine can redraw it
        job = client.post("/api/voice/takes", json={"text": "SHORT take. It is the cat."}).json()
        take = wait(job["id"])["voice"]
        assert [s["live"] for s in fake.seen[-2:]] == [True, False] and take["retaken"]["played"][1]["action"] == "redraw"
        assert take["verify"][1]["action"] == "trim" and take["streamed"] is False and "made again without playing" in wait(job["id"])["log"]
        # not played as it is made: no --stream, so the engine may draw a part again
        job = client.post("/api/voice/takes", json={"text": "Quiet.", "live": False}).json()
        assert wait(job["id"])["status"] == "done" and fake.seen[-1]["live"] is False
        assert voice.estimate("One two three.", False, True) < voice.estimate("One two three.", False, False) + 1  # no Whisper after
    finally:
        voice._backend, voice.qa.ready, voice.qa.transcribe = had, had_ready, had_transcribe


MLX_SERVE = r"""
import json, os, struct, sys, wave
args = sys.argv[1:]
assert args[:3] == ["-I", "-m", "tensorfold.drama3"] and args[-1] == "--serve" and "--model" in args
log = open(os.environ["FAKE_DRAMA3_LOG"], "a")
print("[drama3] loading", file=sys.stderr, flush=True)
print(json.dumps({"ready": True, "engine": "tensorfold-drama3-mlx", "load_s": 0.1, "verify": True, "stream": True}), file=sys.stderr, flush=True)
for line in sys.stdin:
    request = json.loads(line)
    log.write(line); log.flush()
    samples = struct.pack("<h", 9) * 44100
    if request.get("pcm"):
        with open(request["pcm"], "wb") as pcm:
            pcm.write(samples)
    with wave.open(request["wav"], "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(samples)
    short = "SHORT" in request["text"] and request.get("stream")
    verify = [{"words": 4, "matched": 2 if short else 4, "missing": ["the", "cat"] if short else [], "action": "redraw" if short else "ok",
               "attempts": 1 if short else 2, "heard": "It is", "asr_s": 0.1}]
    print(json.dumps({"ok": True, "id": request.get("id"), "frames": 21, "eos": True, "audio_s": 1.0, "first_audio_ms": 210.0, "total_ms": 400.0,
                      "fps": 80.0, "rtf": 0.4, "wav": request["wav"], "pcm": request.get("pcm"), "stream": bool(request.get("stream")),
                      "chunks": [{"finish": "stop"}], "finish": ["stop"], "stop_rule": [False], "verify": verify, "peak_memory_gb": 47.5,
                      **({"ref_texts": ["Hello there."], "ref_asr_ms": 90.0} if request.get("ref_audio") and not request.get("ref_text") else {})}),
          file=sys.stderr, flush=True)
"""


def fake_mlx_server(folder: Path) -> "voice.Mlx":
    """An Mlx backend whose venv python is a stand-in for `python -m tensorfold.drama3 --serve` (drama3-mlx 92c09a8e)."""

    python = folder / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!{sys.executable}\n" + MLX_SERVE)
    python.chmod(0o755)
    os.environ.update(DRAMA3_VENV=str(folder / "venv"), FAKE_DRAMA3_LOG=str(folder / "requests.jsonl"))
    try:
        one = voice.Mlx()
    finally:
        os.environ.pop("DRAMA3_VENV")
    one.known, one.streams, one.serves, one.NEED_GB = (time.time(), ""), True, True, 0
    return one


def test_dialogue_turns_go_on_lines_of_their_own():
    assert voice.dialogue("Hello there.") == "Hello there."
    assert voice.dialogue("<|speaker:0|> Hi. <|speaker:0|> Again.") == "<|speaker:0|> Hi.\n<|speaker:0|> Again."
    text = "[a direction] <|speaker:0|> [whispering] Did you? <|speaker:1|> No.  <|speaker:0|>Yes.\n<|speaker:1|> Fine."
    assert voice.dialogue(text) == "[a direction] <|speaker:0|> [whispering] Did you?\n<|speaker:1|> No.\n<|speaker:0|>Yes.\n<|speaker:1|> Fine."
    assert voice.dialogue(voice.dialogue(text)) == voice.dialogue(text)


def test_an_mlx_take_runs_on_the_served_engine_plays_while_made_and_is_retaken_when_short():
    folder = Path(tempfile.mkdtemp())
    one = fake_mlx_server(folder)
    had, had_ready, had_transcribe = voice._backend, voice.qa.ready, voice.qa.transcribe
    try:
        voice._backend = one
        voice.qa.ready = lambda: {"transcript": True, "runs": True, "missing": []}
        voice.qa.transcribe = lambda clip: (_ for _ in ()).throw(AssertionError("transcribed twice"))
        state = client.get("/api/voice/state").json()
        assert state["live"] is True and state["server"]["loaded"] is False
        text = "<|speaker:0|> [whispering] Did you hear that? <|speaker:1|> It is the cat. " + "And then more words follow here. " * 20
        job = client.post("/api/voice/takes", json={"text": text, "seed": 4}).json()
        body = client.get(f"/api/voice/takes/{job['id']}/live").content
        assert body[:4] == b"RIFF" and len(body) == 44 + 88200, len(body)
        take = wait(job["id"])["voice"]
        sent = requests_of(folder)
        assert len(sent) == 1  # the engine chunks long text itself: one request
        assert sent[0]["text"].startswith("<|speaker:0|> [whispering] Did you hear that?\n<|speaker:1|> It is the cat.")
        assert sent[0]["stream"] is True and sent[0]["pcm"].endswith("0.pcm") and sent[0]["seed"] == 4 and sent[0]["id"] == job["id"]
        assert take["backend"] == "mlx" and take["streamed"] is True and take["first_audio_ms"] == 210.0 and take["load_s"] is not None
        assert take["frames_per_s"] == 80.0 and take["peak_memory_gb"] == 47.5 and take["asr"]["by"] == "engine" and take["asr"]["score"] == 100
        assert one.loaded() and voice.lock_owner().startswith("studio-voice drama3")
        # a part short while it played: asked again, not streamed, on the same server
        pid = one.process.pid
        job = client.post("/api/voice/takes", json={"text": "SHORT: it is the cat.", "ref_audio": None}).json()
        take = wait(job["id"])["voice"]
        again = requests_of(folder)[-2:]
        assert again[0]["stream"] is True and again[1]["stream"] is False and "pcm" not in again[1] and one.process.pid == pid
        assert take["retaken"]["played"][0]["action"] == "redraw" and take["verify"][0]["action"] == "ok" and take["streamed"] is False
        assert take["load_s"] is None
        # a clip to clone without its words: the engine hears it, the app's Whisper does not run first
        one.hears = True
        clip = server.HOME / "uploads" / "mlx-ref.wav"
        with wave.open(str(clip), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(struct.pack("<h", 0) * 44100 * 3)
        job = client.post("/api/voice/takes", json={"text": "Cloned.", "ref_audio": "uploads/mlx-ref.wav", "ref_text": "", "consent": True}).json()
        done = wait(job["id"])
        assert done["status"] == "done", done
        sent = requests_of(folder)[-1]
        assert sent["ref_text"] == "" and sent["ref_audio"].endswith(".ref.wav") and done["voice"]["reference"]["text"] == "Hello there."
        # "live": false: one request, not streamed
        wait(client.post("/api/voice/takes", json={"text": "Quiet.", "live": False}).json()["id"])
        assert requests_of(folder)[-1]["stream"] is False
        assert voice.release("a video job needs the GPU") and not one.loaded() and not voice.LOCK.exists()
    finally:
        one.release("test over")
        voice._backend, voice.qa.ready, voice.qa.transcribe = had, had_ready, had_transcribe


def test_an_mlx_engine_without_serve_runs_once_a_take():
    fake = FakeStreaming(Path(tempfile.mkdtemp()))
    assert not hasattr(fake, "serving")  # the per-take path, as 2.7.6 (test_an_mlx_take_plays_while_made_...)
    one = voice.Mlx()
    one.known, one.streams, one.serves = (time.time(), ""), True, False
    assert one.serving() is False
    command = one.command(Path("t.txt"), Path("o.wav"), Path("s.json"), None, 1, {"temperature": 1.0, "top_p": 0.9, "top_k": 30}, live=True)
    assert "--stream" in command and "--serve" not in command


def test_a_cuda_take_with_missing_words_is_drawn_again_not_played_and_the_best_kept():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    had, had_ready, had_transcribe = voice._backend, voice.qa.ready, voice.qa.transcribe
    text = "<|speaker:0|> Did you hear that noise downstairs? <|speaker:1|> It's probably just the cat. <|speaker:0|> Cats don't knock on doors."
    full, short = "Did you hear that noise downstairs? It's probably just the cat. Cats don't knock on doors.", "Did you hear that noise downstairs? It's probably just the cat."
    try:
        voice._backend = one
        voice.qa.ready = lambda: {"transcript": True, "runs": True, "missing": []}
        voice.qa.transcribe = lambda clip: full if ".draw2" in str(clip) else short  # the first two draws drop the last turn
        job = client.post("/api/voice/takes", json={"text": text, "seed": 7}).json()
        done = wait(job["id"])
        take = done["voice"]
        sent = requests_of(folder)[-3:]
        assert [r["seed"] for r in sent] == [7, 1007, 2007] and "pcm" in sent[0] and "pcm" not in sent[1] and "pcm" not in sent[2]
        assert take["verify"][0]["action"] == "ok" and take["verify"][0]["attempts"] == 3 and take["asr"] == {**take["asr"], "score": 100, "by": "whisper"}
        assert take["retaken"]["played"][0]["action"] == "redraw" and take["retaken"]["played"][0]["missing"] == ["cats", "don't", "knock", "on", "doors"]
        assert take["streamed"] is False and (server.HOME / take["path"]).is_file() and not list((server.HOME / "voice").glob(f"{job['id']}.draw*"))
        assert "words missing" in done["log"]
        # every draw short: the best one is kept after two draws again, and said
        voice.qa.transcribe = lambda clip: short
        take = wait(client.post("/api/voice/takes", json={"text": text, "seed": 1}).json()["id"])["voice"]
        assert take["verify"][0]["action"] == "redraw" and take["verify"][0]["attempts"] == 3 and take["asr"]["score"] < 100
        # the whole text heard at once: no draw again
        voice.qa.transcribe = lambda clip: full
        count = len(requests_of(folder))
        take = wait(client.post("/api/voice/takes", json={"text": text, "seed": 2}).json()["id"])["voice"]
        assert len(requests_of(folder)) == count + 1 and take["retaken"] is None and take["streamed"] is True and take["verify"][0]["attempts"] == 1
    finally:
        one.release("test over")
        voice._backend, voice.qa.ready, voice.qa.transcribe = had, had_ready, had_transcribe


def wav_of(path: Path, *runs: tuple[float, int]) -> Path:
    """A test WAV of (seconds, amplitude) runs: 8000 is speech-loud (-12 dBFS), 300 the stall's hum (-41 dBFS)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        for seconds, amplitude in runs:
            w.writeframes(struct.pack("<hh", amplitude, -amplitude) * int(22050 * seconds))
    return path


def test_a_stall_is_quiet_after_speech_that_lasts():
    folder = Path(tempfile.mkdtemp())
    assert voice.stall(wav_of(folder / "a.wav", (2, 8000), (2.4, 0), (2, 8000), (0.5, 0))) is None  # a long pause of its own
    assert voice.stall(wav_of(folder / "b.wav", (9.2, 8000), (26.9, 300))) == 9.2  # the hum to the frame cap
    assert voice.stall(wav_of(folder / "c.wav", (3, 8000), (3.5, 0))) == 3.0  # silence at the end
    assert voice.stall(wav_of(folder / "d.wav", (3, 8000), (9, 300), (3, 8000))) == 3.0  # a chunk stalled, the next went on
    assert voice.stall(wav_of(folder / "e.wav", (0.5, 8000), (10, 0))) is None  # under a second of speech
    assert voice.stall(wav_of(folder / "f.wav", (3, 0))) is None and voice.stall(folder / "missing.wav") is None


def test_a_cuda_part_that_stalls_is_drawn_again_with_the_next_seed_not_played():
    folder = Path(tempfile.mkdtemp())
    one = fake_cuda(folder)
    had = voice._backend
    try:
        voice._backend = one
        done = wait(client.post("/api/voice/takes", json={"text": "The tower STALL has not chimed in years.", "seed": 8, "asr": False}).json()["id"])
        take, sent = done["voice"], requests_of(folder)[-2:]
        assert [r["seed"] for r in sent] == [8, 9] and "pcm" in sent[0] and "pcm" not in sent[1]
        assert take["stalled"] == [{"part": 1, "at_s": 2.0, "redraws": 1, "seed": 9, "still": None}]
        assert "went quiet after 2.0 s" in done["log"] and not list((server.HOME / "voice").glob(f"{done['id']}*.stall*"))
        assert voice.stall(server.HOME / take["path"]) is None
        # a part that stalls on every seed: three draws again, then the first kept and said
        count = len(requests_of(folder))
        take = wait(client.post("/api/voice/takes", json={"text": "ALWAYS STALL here.", "seed": 3, "asr": False}).json()["id"])["voice"]
        assert [r["seed"] for r in requests_of(folder)[count:]] == [3, 4, 5, 6]
        assert take["stalled"] == [{"part": 1, "at_s": 2.0, "redraws": 3, "seed": 3, "still": 2.0}]
        # a take that never stalls is asked once
        count = len(requests_of(folder))
        take = wait(client.post("/api/voice/takes", json={"text": "No trouble here.", "seed": 4, "asr": False}).json()["id"])["voice"]
        assert len(requests_of(folder)) == count + 1 and take["stalled"] is None
    finally:
        one.release("test over")
        voice._backend = had


def test_a_one_request_take_that_stalls_is_made_again_with_the_next_seed():
    folder = Path(tempfile.mkdtemp())

    class OneRequest:  # the Mac's MLX server: the whole take in one request
        seeds = []

        def make(self, job, text, wav, reference, seed, sampling, live=True):
            self.seeds.append((seed, live))
            wav_of(wav, (2, 8000), (5, 300)) if seed < 13 else wav_of(wav, (4, 8000))
            return {"audio_s": 7.0 if seed < 13 else 4.0, "first_audio_ms": 90.0 + seed}

    job = {"log": ""}
    params = {"text": "A long line.", "seed": 11, "temperature": 1.0, "top_p": 0.9, "top_k": 30}
    wav = wav_of(folder / "t.wav", (2, 8000), (5, 300))
    one = OneRequest()
    measured = voice.unstalled(job, one, params, wav, None, {"audio_s": 7.0, "first_audio_ms": 80.0, "load_s": 5.0})
    assert one.seeds == [(12, False), (13, False)] and measured["audio_s"] == 4.0 and measured["first_audio_ms"] == 80.0
    assert measured["stalled"] == {"at_s": 2.0, "redraws": 2, "seed": 13, "still": None} and voice.stall(wav) is None
    assert not list(folder.glob("*.stall*")) and job["log"].count("a Drama 3 stall") == 2


def test_the_word_check_is_the_engines():
    import wordcheck
    text = "<|speaker:0|> [sighs] It's probably just the cat. <|speaker:1|> Cats don't knock on doors."
    assert wordcheck.check_text(text, "It's probably just the cat. Cats don't knock on doors.")["action"] == "ok"
    assert wordcheck.check_text(text, "It's probably just the cat. Cats don't knock on the")["reason"] == "the last word is missing"
    assert wordcheck.check_text("It costs 41 dollars.", "It costs forty-one dollars.")["action"] == "ok"
    assert wordcheck.check_text("Built with TensorFold.", "Built with Tensor Fold.")["action"] == "ok"  # a near-exact merge
    assert wordcheck.check_text("Go to the cat.", "Go to the.")["action"] == "redraw"  # the last content word


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)


def test_a_voice_take_waits_for_memory_a_stopped_server_is_giving_back(monkeypatch):
    """ComfyUI stopped by its memory floor a moment before: its memory comes back over some seconds, and a take queued
    then was refused with half of it still away (DGX Spark head, 2026-10-20). It is waited for now."""

    voice = server.voice
    readings = iter([40.0, 80.0])
    monkeypatch.setattr(voice, "mem_available_gb", lambda: next(readings, 80.0))
    monkeypatch.setattr(voice, "comfy_up", lambda port=None: False)
    waited = []
    monkeypatch.setattr(voice, "settle", lambda: waited.append(1))
    one = voice.Served.__new__(voice.Served)
    one.NEED_GB, one.title = 50.0, "Drama 3"
    one.make_room({})  # 40 GB at first, 80 once it settled: no refusal
    assert waited
    readings = iter([40.0, 41.0])
    monkeypatch.setattr(voice, "mem_available_gb", lambda: next(readings, 41.0))
    try:
        one.make_room({})
        raise AssertionError("refused expected")
    except RuntimeError as error:
        assert "needs about 50 GB" in str(error)


def test_an_engine_the_kernel_wont_release_still_gives_the_gpu_lock_back(monkeypatch, tmp_path):
    """A SIGKILL to a process stuck in the kernel (the way a wedged GPU leaves it) is not always waitable. That must
    not hold stop()'s actual promise hostage -- the lock back and ownership cleared -- or every later take waits on
    the GPU lock of an owner that is gone until the app itself restarts."""

    voice = server.voice
    monkeypatch.setattr(voice, "LOCK", tmp_path / "studio-gpu.lock.d")
    monkeypatch.setattr(voice, "settle", lambda: None)
    monkeypatch.setattr(voice, "mem_available_gb", lambda: 80.0)
    one = voice.Served.__new__(voice.Served)
    one.mutex = threading.RLock()
    one.note = lambda *args, **more: None
    one.owner = "drama3@studio"
    voice.LOCK.mkdir(parents=True)
    (voice.LOCK / "owner").write_text("drama3@studio")

    class Corpse:  # poll() alive, and no wait that ever returns: kill-guard territory
        class stdin:
            @staticmethod
            def close():
                pass

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout: float = 0):
            raise subprocess.TimeoutExpired("drama3-server", timeout)

    one.process = Corpse()
    one.stop("the engine hung")  # must not raise out of the wedged wait
    assert one.process is None and one.owner == "" and not voice.LOCK.exists()


def test_voice_setup_stops_before_the_engine_when_the_weights_folder_is_not_writable(tmp_path):
    """A root-owned ~/models: setup-voice.sh fails at once and says how to fix it, before the engine step, instead of
    building the engine and leaving the checkpoint missing for the next boot to find."""

    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    script = (ROOT / "scripts" / "setup-voice.sh").read_text()
    function = script[script.index("writable_for() {"):]
    function = function[:function.index("\n}\n") + 3]
    assert script.index('writable_for "$MODEL"; fi\n  step "TensorFold\'s Drama 3 engine') > 0  # Linux: before the engine is built
    locked = tmp_path / "models"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        run = lambda target: subprocess.run(["bash", "-c", 'die() { echo "FATAL: $*" >&2; exit 1; }\n' + function + f'writable_for "{target}" && echo fine'],
                                            capture_output=True, text=True)
        stopped = run(locked / "drama-3")
        assert stopped.returncode == 1 and "is not writable by" in stopped.stderr and "DRAMA3_CHECKPOINT" in stopped.stderr
        assert run(tmp_path / "mine" / "drama-3").stdout.strip() == "fine"
    finally:
        locked.chmod(0o755)


def mac_with(gib: int, monkeypatch):
    """This process as a Mac with ``gib`` GB of memory: sysctl answers that, and the engine's own probe does not answer."""

    def run(argv, *args, **kwargs):
        if argv[:2] == ["sysctl", "-n"]:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{gib << 30}\n", stderr="")
        raise OSError("the probe is not run here")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", run)


def test_drama3_on_a_mac_needs_96_gb(monkeypatch):
    """The Studio's minimum for a Mac is 96 GB (64 up to 4.3.0): the Voice tab says so on a smaller one."""

    folder = Path(tempfile.mkdtemp())
    python = folder / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    for name in ("config.json", "codec.safetensors"):
        (folder / "model").mkdir(exist_ok=True)
        (folder / "model" / name).write_text("{}")
    monkeypatch.setenv("DRAMA3_VENV", str(folder / "venv"))
    monkeypatch.setenv("DRAMA3_MODEL", str(folder / "model"))
    assert voice.MEMORY_GB == 96
    for gib in (64, 95):
        mac_with(gib, monkeypatch)
        assert voice.Mlx().look() == f"This Mac has {gib} GB of memory; Drama 3 needs about 52 GB free, so 96 GB or more."
    mac_with(96, monkeypatch)
    assert voice.Mlx().look().startswith("The Drama 3 environment did not answer")  # past the memory, to the engine
