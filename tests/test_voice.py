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


def test_the_cuda_backend_says_it_is_not_there_yet():
    why = voice.Cuda().check()
    assert "not available yet" in why.lower() and "DGX Spark" in why
    had = voice._backend
    try:
        voice._backend = voice.Cuda()
        state = client.get("/api/state").json()["voice"]
        assert state["available"] is False and "not available yet" in state["why"].lower() and state["credit"] == "Built with Fish Audio"
        answer = client.post("/api/voice/takes", json={"text": "Hello."})
        assert answer.status_code == 400 and "not available yet" in answer.json()["error"].lower()
    finally:
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
