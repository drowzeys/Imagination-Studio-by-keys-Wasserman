"""Voice, Fish Audio Drama 3 (app/voice.py): python3 tests/test_voice.py. No GPU, no model, no network: the engine's
command line is stood in for by a small script that writes a WAV and its stats."""
import json
import os
import struct
import sys
import tempfile
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
    assert "codec_convert.py" in one.check() and "DRAMA3_CUDA_CODEC" in one.check()
    one.known, one.model = None, folder / "nowhere"
    assert "No Drama 3 checkpoint" in one.check()
    one.known, one.bin = None, folder / "nothing"
    why = one.check()
    assert "drama3-cuda" in why and "DRAMA3_CUDA_BIN" in why
    had = voice._backend
    try:
        voice._backend = one
        state = client.get("/api/state").json()["voice"]
        assert state["available"] is False and state["backend"] == "cuda" and state["credit"] == "Built with Fish Audio"
        assert state["server"]["loaded"] is False and state["live"] is True
        answer = client.post("/api/voice/takes", json={"text": "Hello."})
        assert answer.status_code == 400 and "DRAMA3_CUDA_BIN" in answer.json()["error"]
    finally:
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
        job = client.post("/api/voice/takes", json={"text": "Again.", "asr": False}).json()
        take = wait(job["id"])["voice"]
        assert one.process.pid == pid and take["load_s"] is None
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
            had_room = server.memory_guard.room_mb
            try:
                server.memory_guard.room_mb = lambda: 1000
                assert server.memory_of({}, {"WIDTH": 864, "HEIGHT": 480}, 121, False)["available_mb"] == voice.room_mb()
            finally:
                server.memory_guard.room_mb = had_room
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
    project = {"id": "p-v", "name": "V", "defaults": {"durationSec": 8}, "tf": {"res": "480p", "quality": "fh8"},
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
        project["scenes"][0]["shots"][0]["tf"]["start"] = "text"  # not on cast stills: never audio alone
        project["voices"][0]["drama"]["use"] = True
        assert "reference_audios" not in slate.compile_shot(project, "sh")["plan"]
    finally:
        slate.ready = had


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
