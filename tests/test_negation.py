"""app/negation.py: python3 -m pytest tests/test_negation.py (or python3 tests/test_negation.py). No GPU, no network.
What a video prompt forbids by name is listed for the user. A sentence the user wrote is never rewritten. A raw
prompt with no spoken line gains the Studio's own ambient line under its own heading."""
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import negation as N  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
phrases = lambda text: [one["phrase"] for one in N.find(text)]


def test_the_three_ways_of_forbidding_a_thing():
    assert phrases("A bedroom with no photographs.") == ["no photographs"]
    assert phrases("One take without any cut.") == ["without any cut"]
    assert phrases("She does not smile.") == ["not smile"]
    assert phrases("They NEVER look at the camera, and don't turn round.") == ["NEVER look at the camera", "don't turn round"]
    assert N.find("No close-up of her face.")[0]["thing"] == "close-up face"
    # the production run's own line, which hung the wall with photographs
    assert "No photographs or portraits" in phrases("She turns from the window. No photographs or portraits appear anywhere.")


def test_what_is_not_a_negation_is_left_alone():
    assert phrases("A notable nobleman ties a knot north of Nottingham.") == []
    assert phrases("The fox is not only quick but quiet; no doubt it is night.") == []
    assert phrases("A man walks his dog on the beach at noon.") == []
    # each phrase once
    assert phrases("no text. A sign. No text.") == ["no text"]


def test_the_studios_own_sentences_forbid_nothing_by_name():
    """The app's own words for a clip without speech and for a locked camera say what is there: the check finds
    nothing in a prompt the app wrote, in the picture or in the sound, with nothing passed over."""

    had, N.OWN = N.OWN, ()
    try:
        prompt = server.compose({"scene": "A quiet harbour at dawn.", "action": "A boat leaves."}, False)
        assert "Nobody speaks" not in prompt and "No voices" not in prompt and "Only ambient sound is heard." in prompt
        assert "Mouths" not in prompt  # nobody is in the shot: a mouth is not named into it
        assert N.find(prompt.replace("non_diegetic_music: N/A", "")) == []
        people = server.compose({"scene": "A woman waits on the quay.", "action": "She watches a boat leave."}, False)
        assert "Mouths stay closed." in people and N.find(people.replace("non_diegetic_music: N/A", "")) == []
        spoken = server.compose({"scene": "A kitchen.", "dialogue": [{"text": "Good morning."}]}, False)
        assert "stays silent" in spoken and "After the spoken lines only ambient sound is heard." in spoken
        assert N.find(spoken.replace("non_diegetic_music: N/A", "").replace("Good morning.", "")) == []
        # a soundscape that already says it, in the old words or the new, is not told twice
        assert server.compose({"scene": "A hall.", "soundscape": "Rain. No speech."}, False).count("ambient sound") == 0
        assert server.compose({"scene": "A hall.", "soundscape": "Rain. Only ambient sound is heard."}, False).count("Only ambient sound") == 1
        # a clip from a reference picture: its subject is a person unless the fields say otherwise
        ref = server.compose_references({"scene": "A cafe.", "subjects": ["a woman in a red coat"]}, 1)
        assert "Mouths stay closed." in ref and "Only ambient sound is heard." in ref and N.find(ref) == []
        assert "Mouths" not in server.compose_references({"scene": "A cafe.", "subjects": ["a red teapot"], "people": False}, 1)
    finally:
        N.OWN = had
    # prompts written before this change still pass: the old sentences are passed over
    assert N.check("integrated_multimodal_description: [Shot 1] A hall. Nobody speaks.\n\noverall_soundscape: Rain. No voices, no speech.")["negations"] == []
    # the soundscape and the music are the sound track's own fields
    assert N.check("integrated_multimodal_description: [Shot 1] A hall.\n\noverall_soundscape: Rain, no thunder.\n\nnon_diegetic_music: N/A")["negations"] == []


def test_compose_lists_them_by_box_and_sends_the_prompt_as_written():
    fields = {"scene": "A dark bedroom with no photographs on the walls.", "camera": "A steady shot.", "action": "She never looks at the camera."}
    reply = client.post("/api/compose", json={"fields": fields, "first_frame": False}).json()
    assert [(one["phrase"], one["field"]) for one in reply["negations"]] == [("no photographs on the walls", "Scene"), ("never looks at the camera", "Action")]
    assert "no negative prompt" in reply["reason"]
    assert "no photographs on the walls" in reply["prompt"] and "never looks at the camera" in reply["prompt"]  # nothing rewritten
    clean = client.post("/api/compose", json={"fields": {"scene": "A dark bedroom with plain walls."}, "first_frame": False}).json()
    assert clean["negations"] == [] and clean["reason"] == ""
    # the reference form goes through the same check
    reply = client.post("/api/compose", json={"fields": {"scene": "A street without cars."}, "references": 1}).json()
    assert [one["phrase"] for one in reply["negations"]] == ["without cars"]


def test_a_prompt_written_by_hand_is_checked_before_it_is_queued():
    reply = client.post("/api/prompt/check", json={"prompt": "[Shot 1] A hall. No cut, no close-up. Nobody speaks.\n\noverall_soundscape: No voices, no speech."}).json()
    assert [one["phrase"] for one in reply["negations"]] == ["No cut", "no close-up"] and reply["reason"]
    assert client.post("/api/prompt/check", json={"prompt": "A hall at dusk."}).json() == {"negations": [], "reason": ""}


def test_a_raw_prompt_gains_the_ambient_line_and_keeps_its_own_words():
    """The sentence is the app's own. A prompt the app already composed, a spoken line, a heading, and a
    reference video's own soundtrack are left as they were given."""

    raw = "A bike rides a dune. No cut, no logo."
    out = N.ensure_ambient(raw)
    assert raw in out
    assert out.startswith("integrated_multimodal_description: [Shot 1] ")
    assert "overall_soundscape: Only ambient sound is heard." in out
    assert out.endswith("non_diegetic_music: N/A\n")
    assert N.QUIET_SOUND == server.QUIET_SOUND
    made = server.compose({"scene": "A quiet harbour at dawn.", "action": "A boat leaves."}, False)
    assert N.ensure_ambient(made) == made
    spoken = "She says: <d>[English] Hello.</d>\n"
    assert N.ensure_ambient(spoken) == spoken
    headed = "integrated_multimodal_description: [Shot 1] A hall.\n\noverall_soundscape: No speech.\n"
    assert N.ensure_ambient(headed) == headed
    assert N.ensure_ambient(raw, video_sound=True) == raw
    # a music heading is kept, and the ambient line is still its own heading
    music = "integrated_multimodal_description: [Shot 1] A hall.\n\nnon_diegetic_music: N/A"
    prepared = N.ensure_ambient(music)
    assert "non_diegetic_music: N/A" in prepared and prepared.count("overall_soundscape:") == 1
    assert N.ensure_ambient("") == ("integrated_multimodal_description: [Shot 1]\n\n"
                                    "overall_soundscape: Only ambient sound is heard.\n\nnon_diegetic_music: N/A\n")


def test_a_reference_a_plain_prompt_and_a_first_frame_lead():
    """The three cases from the review: a voice reference, REF_PLAIN=1, and the first-frame lead."""

    voice = "Use <Picture 1> as the person. The person looks into the lens and says the line in the voice of <Audio 1>."
    assert N.ensure_ambient(voice) == voice
    assert N.ensure_ambient("A bike rides a dune.", references=True) == "A bike rides a dune."
    assert N.ensure_ambient("<Video 1> with its person replaced.") == "<Video 1> with its person replaced."
    plain = "A rider stands beside a motorbike."
    assert N.ensure_ambient(plain, plain=True) == plain
    lead = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    scene = "A lone rider stands beside a motorbike. No cut, no logo."
    out = N.ensure_ambient(f"{lead}\n\n{scene}")
    assert out.startswith(lead + "\n\nintegrated_multimodal_description: [Shot 1] ")
    assert scene in out and "overall_soundscape: Only ambient sound is heard." in out
    assert out.index(lead) < out.index("integrated_multimodal_description:")
    # the lead's own <Picture 1> is the opening frame, not a reason to skip the heading
    assert "<Picture 1>" in out.split("integrated_multimodal_description:", 1)[0]


def test_spark_video_sh_prepares_a_raw_prompt_and_leaves_a_finished_one():
    """spark/video.sh is what scripts/video.sh runs on Linux. A fake renderer records the prompt it was given."""

    folder = Path(tempfile.mkdtemp())
    prefix = folder / "prefix"
    (prefix / "venv/bin").mkdir(parents=True)
    fake = prefix / "venv/bin/python"
    calls = folder / "calls"
    fake.write_text("#!/bin/bash\nprintf '%s\\0' \"$@\" > \"$CALLS\"\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def render(prompt_file: Path, **env):
        calls.unlink(missing_ok=True)
        run = {**os.environ, "PREFIX": str(prefix), "STUDIO_GPU": "0", "ENGINE": "fasth3", "CALLS": str(calls),
               "PROMPT_FILE": str(prompt_file)}
        for name in ("REF_VIDEO_SOUND", "REF_PLAIN", "REF_IMAGES", "REF_VIDEOS", "REF_AUDIOS", "REF_COUNT"):
            run.pop(name, None)
        run.update(env)
        done = subprocess.run(["bash", str(ROOT / "spark/video.sh"), "", str(folder / "out.mp4")],
                              env=run, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        args = calls.read_bytes().split(b"\0")
        return done, args

    raw = folder / "raw.txt"
    raw.write_text("A bike rides a dune. No cut, no logo.\n")
    done, args = render(raw)
    assert b"--prompt" in args and b"--prompt-file" not in args
    text = args[args.index(b"--prompt") + 1].decode()
    assert "A bike rides a dune. No cut, no logo." in text
    assert "overall_soundscape: Only ambient sound is heard." in text
    assert "no spoken line and no overall_soundscape heading" in done.stdout
    assert raw.read_text() == "A bike rides a dune. No cut, no logo.\n"

    headed = folder / "headed.txt"
    headed.write_text("integrated_multimodal_description: [Shot 1] A hall.\n\noverall_soundscape: Rain.\n\nnon_diegetic_music: N/A\n")
    done, args = render(headed)
    assert args[args.index(b"--prompt-file") + 1].decode() == str(headed)
    assert "no spoken line" not in done.stdout

    voiced = folder / "voiced.txt"
    voiced.write_text("She says: <d>[English] Hello.</d>\n")
    _, args = render(voiced)
    assert args[args.index(b"--prompt-file") + 1].decode() == str(voiced)

    _, args = render(raw, REF_VIDEO_SOUND="1")
    assert args[args.index(b"--prompt-file") + 1].decode() == str(raw)

    # a reference with a voice, and REF_PLAIN=1, stay as written. The files only have to exist.
    face, audio = folder / "face.png", folder / "voice.wav"
    face.write_bytes(b"x")
    audio.write_bytes(b"x")
    nodes = folder / "nodes.txt"
    nodes.write_text("Use <Picture 1> as the person. The person looks into the lens and says the line in the voice of <Audio 1>.\n")
    done, args = render(nodes, REF_IMAGES=str(face), REF_AUDIOS=str(audio))
    assert args[args.index(b"--prompt-file") + 1].decode() == str(nodes)
    assert "Only ambient sound is heard" not in done.stdout
    done, args = render(raw, REF_PLAIN="1")
    assert args[args.index(b"--prompt-file") + 1].decode() == str(raw)
    assert "Only ambient sound is heard" not in done.stdout

    lead = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    opening = folder / "opening.txt"
    opening.write_text(f"{lead}\n\nA lone rider stands beside a motorbike. No cut, no logo.\n")
    _, args = render(opening)
    text = args[args.index(b"--prompt") + 1].decode()
    assert text.startswith(lead)
    assert text.index(lead) < text.index("integrated_multimodal_description:")
    assert "overall_soundscape: Only ambient sound is heard." in text


def test_a_prompt_given_on_the_command_line_keeps_its_last_newline_and_survives_a_failed_python():
    """scripts/video.sh hands the prompt over as an argument. One that is left as written, also when it ends in a
    newline, is not said to be changed; and a python3 that fails leaves the prompt as it was, not empty."""

    folder = Path(tempfile.mkdtemp())
    prefix = folder / "prefix"
    (prefix / "venv/bin").mkdir(parents=True)
    fake = prefix / "venv/bin/python"
    calls = folder / "calls"
    fake.write_text("#!/bin/bash\nprintf '%s\\0' \"$@\" > \"$CALLS\"\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def render(prompt, path=None):
        run = {k: v for k, v in os.environ.items()
               if k not in ("REF_VIDEO_SOUND", "REF_PLAIN", "REF_IMAGES", "REF_VIDEOS", "REF_AUDIOS", "REF_COUNT", "PROMPT_FILE")}
        run.update(PREFIX=str(prefix), STUDIO_GPU="0", ENGINE="fasth3", CALLS=str(calls))
        if path:
            run["PATH"] = path
        done = subprocess.run(["bash", str(ROOT / "spark/video.sh"), prompt, str(folder / "out.mp4")],
                              env=run, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        args = calls.read_bytes().split(b"\0")
        return done, args[args.index(b"--prompt") + 1].decode()

    headed = "integrated_multimodal_description: [Shot 1] A hall.\n\noverall_soundscape: Rain.\n\nnon_diegetic_music: N/A\n"
    done, sent = render(headed)
    assert sent == headed and "no spoken line" not in done.stdout
    # a python3 that fails: the prompt goes as written
    broken = folder / "bin"
    broken.mkdir()
    (broken / "python3").write_text("#!/bin/bash\nexit 1\n")
    (broken / "python3").chmod(0o755)
    done, sent = render("A bike rides a dune.", path=f"{broken}:{os.environ['PATH']}")
    assert sent == "A bike rides a dune." and "sent as written" in done.stderr


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)


def test_a_spoken_line_forbids_nothing_in_the_picture():
    # a hand-written prompt quotes its dialogue; a composed one tags it: the words are heard, not shown
    assert phrases("Dialogue (Oren, solemnly): \u201cI didn't keep it, and I never will.\u201d") == []
    assert phrases('He says, "Do not leave." She does not smile.') == ["not smile"]
    assert phrases("The speaker says: <d>[English] No photographs, please.</d> A bare wall.") == []
