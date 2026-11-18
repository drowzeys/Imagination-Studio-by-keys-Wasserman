"""app/script_lines.py: python3 -m pytest tests/test_script_lines.py. No GPU, no network.
An uploaded prompt or script gives the Words / Sound step its Voice / Line rows; the words are kept as written."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import script_lines as L  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
here = TestClient(server.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")


def test_a_prompt_the_app_wrote_comes_back_as_its_rows():
    fields = {"scene": "A bakery at dawn.", "action": "The baker lifts a loaf.", "soundscape": "Oven hum.", "music": "Soft piano.",
              "dialogue": [{"voice": "a calm, slightly raspy male voice", "language": "English", "text": "Fresh out of the oven.", "sung": False},
                           {"voice": "a bright voice", "language": "French", "text": "La la la.", "sung": True}]}
    found = L.extract(server.compose(fields, True))
    assert found["format"] == "h3"
    assert [(d["voice"], d["language"], d["text"], d["sung"]) for d in found["dialogue"]] == [
        ("a calm, slightly raspy male voice", "English", "Fresh out of the oven.", False), ("a bright voice", "French", "La la la.", True)]
    assert found["soundscape"].startswith("Oven hum.") and found["music"] == "Soft piano."


def test_a_reference_prompt_keeps_its_subject():
    found = L.extract("[Shot 1] <Subject 1>, the woman, (S1), in a warm voice, says: <d>[English] Hi there.</d>")["dialogue"]
    assert found[0]["speaker"] == "<Subject 1>" and found[0]["voice"] == "a warm voice" and found[0]["text"] == "Hi there."


def test_a_screenplay():
    found = L.extract("INT. KITCHEN - DAY\n\nSARAH\n(whispering)\nDid you hear that?\n\nTOM (V.O.)\nIt's just the wind.\nGo back to sleep.\n")
    assert found["format"] == "screenplay"
    assert [(d["speaker"], d["voice"], d["text"]) for d in found["dialogue"]] == [
        ("Sarah", "a whispering voice", "Did you hear that?"), ("Tom", "", "It's just the wind. Go back to sleep.")]


def test_names_and_colons_but_not_headings():
    found = L.extract('Scene: a dark kitchen at night\nCamera: slow push in\nSarah (soft, nervous voice): "Did you hear that?"\nTom: It\'s just the wind.')
    assert [(d["speaker"], d["voice"], d["text"]) for d in found["dialogue"]] == [
        ("Sarah", "soft, nervous voice", "Did you hear that?"), ("Tom", "", "It's just the wind.")]


def test_quoted_lines_in_prose():
    found = L.extract('She whispers in a soft, trembling voice, "Is anyone there?" A man answers, "Only me." She sings, "La la."')["dialogue"]
    assert [(d["voice"], d["text"], d["sung"]) for d in found] == [
        ("a soft, trembling voice", "Is anyone there?", False), ("", "Only me.", False), ("", "La la.", True)]
    # a quoted sign is not a line
    assert L.extract('A sign reads "OPEN" above the door.')["dialogue"] == []


def test_nothing_spoken_and_the_route():
    assert L.extract("A cat sleeps on a warm windowsill.") == {"dialogue": [], "format": "", "seconds": None}
    r = client.post("/api/dialogue/extract", json={"text": "BOB\nHello.\n"})
    assert r.status_code == 200 and r.json()["dialogue"][0]["text"] == "Hello."


def test_a_length_the_text_gives_itself():
    assert L.extract("Duration: 20 s\nBOB\nHello.\n")["seconds"] == 20
    assert L.extract("Length of about 1 min. She says, \"Hi.\"")["seconds"] == 60
    assert L.stated_seconds("Twenty people in 5 seconds flat") is None


def test_a_long_video_takes_one_prompt_a_part():
    params = {"prompt": "A walk.", "total_seconds": 30, "preset": "fast", "quality": "standard", "seed": 1}
    for wrong in (["one"], ["one", ""], "one", ["a", "b", "c"]):
        r = here.post("/api/jobs", json={"kind": "long", "title": "t", "params": {**params, "prompts": wrong}})
        assert r.status_code == 400 and "one for each of its 2 parts" in r.json()["error"], r.text


def test_a_shot_list_with_tagged_dialogue():
    text = """# A test scene, 25 seconds, 16:9

output_spec:
16:9 aspect ratio, 25 seconds, 24fps.

[Shot 1] 00:00-00:05
Shot size: Medium close-up.
Camera movement: Fixed camera.
Image: A woman holds a lantern in a stone hallway.
Dialogue (Mira, coldly, with a hint of a smile): \u201cAre you lost?\u201d

[Shot 2] 00:05-00:11
Image: The man looks up.
Dialogue (Oren, voice-over): \u201c\u4f60\u597d\u3002\u201d

[Shot 3] 00:21-00:25
Lyrics (Mira, softly): The tide comes in.
"""
    found = L.extract(text)
    assert found["format"] == "colons" and found["seconds"] == 25
    assert [(d["speaker"], d["voice"], d["text"], d["language"], d["sung"]) for d in found["dialogue"]] == [
        ("Mira", "a voice speaking coldly, with a hint of a smile", "Are you lost?", "English", False),
        ("Oren", "an off-screen voice", "\u4f60\u597d\u3002", "Chinese", False),
        ("Mira", "a voice speaking softly", "The tide comes in.", "English", True)]


def test_a_note_that_already_says_in():
    assert L.described("in a low voice") == "a low voice"


def test_a_cast_list_is_not_dialogue():
    text = """Character design:
Mira: A young woman in a long grey coat, holding a lantern.
Oren: A tired man with a scarf.

[Shot 1] 00:00-00:05
Dialogue (Mira, coldly): \u201cAre you lost?\u201d
"""
    assert [(d["speaker"], d["text"]) for d in L.extract(text)["dialogue"]] == [("Mira", "Are you lost?")]
    # without tagged lines, the cast list is still left out and the plain lines are read
    plain = "Characters:\nMira: A young woman in grey.\n\nMira: \u201cAre you lost?\u201d\nOren: Only me."
    assert [(d["speaker"], d["text"]) for d in L.extract(plain)["dialogue"]] == [("Mira", "Are you lost?"), ("Oren", "Only me.")]


def test_the_sound_and_music_of_any_prompt():
    text = """[Shot 1] 00:00-00:05
Sound: a door creaks.
Dialogue (Mira, coldly): \u201cAre you lost?\u201d

[Shot 2] 00:05-00:10
Sound: footsteps.
Dialogue (Oren): \u201cNo.\u201d

overall_soundscape:
Wind through an empty hall,
a lantern hissing.

non_diegetic_music:
A low cello, quiet under the voices.
"""
    found = L.extract(text)
    assert found["soundscape"] == "Wind through an empty hall, a lantern hissing."
    assert found["music"] == "A low cello, quiet under the voices."
    # a plain heading counts when it appears once; one in every shot is that shot's and is left
    assert L.extract("BOB\nHello.\n\nMusic: soft piano\n")["music"] == "soft piano"
    assert "soundscape" not in L.extract(text.split("overall_soundscape")[0])


def test_a_prompt_with_its_line_breaks_lost():
    text = ("Test prompt, 25 seconds, 16:9, 24fps output_spec: 16:9, 25 seconds. Character design: Mira: a woman in grey. "
            "Oren: a tired man. [Shot 1] 00:00-00:05 Shot size: Medium. Image: Mira holds a lantern, very still "
            "Dialogue (Mira, coldly, hint smile): \u201cAre you lost?\u201d [Shot 2] 00:05-00:11 Image: Oren looks up. "
            "Dialogue (Oren, solemnly): \u201cOnly a little.\u201d He sighs. [Shot 3] 00:21-00:25 Image: they face each other "
            "Dialogue (Oren, softly): \u201cStay.\u201d overall_soundscape: Wind in a stone hall, fabric rustling. "
            "non_diegetic_music: A low cello, only in the gaps between the lines.")
    found = L.extract(text)
    assert found["seconds"] == 25
    assert [(d["speaker"], d["text"]) for d in found["dialogue"]] == [("Mira", "Are you lost?"), ("Oren", "Only a little."), ("Oren", "Stay.")]
    assert found["soundscape"] == "Wind in a stone hall, fabric rustling."
    assert found["music"] == "A low cello, only in the gaps between the lines."
