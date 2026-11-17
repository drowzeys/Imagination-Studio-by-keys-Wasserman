"""server.compose_references: python3 -m pytest tests/test_speakers.py. No GPU, no network.
Two speakers in a clip from a reference video (no pictures) are two subjects, each defined, each named; before,
every line was <Subject 1> and the model gave all the words to the first face it saw."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402

TWO = [{"text": "Who is that?", "voice": "a tense female voice", "subject": 2, "speaker": "the woman in the back seat"},
       {"text": "The man most responsible.", "voice": "a flat male voice", "subject": 1, "speaker": "the driver"}]


def definitions(prompt: str) -> str:
    return prompt.split("summary:")[0]


def test_two_speakers_in_a_reference_video_are_two_defined_subjects():
    prompt = server.compose_references({"scene": "Inside a car.", "video_subjects": [""], "dialogue": TWO}, 0)
    head = definitions(prompt)
    assert "<Subject 2> is the woman in the back seat" in head
    assert "<Subject 2>, the woman in the back seat, (S1)" in prompt
    assert "<Subject 1>, the driver, (S2)" in prompt


def test_a_speaker_with_no_name_is_still_a_different_person():
    lines = [{**TWO[0], "speaker": ""}, {**TWO[1], "speaker": ""}]
    head = definitions(server.compose_references({"scene": "Inside a car.", "video_subjects": [""], "dialogue": lines}, 0))
    assert "<Subject 2> is another person" in head


def test_pictures_keep_their_own_definitions():
    prompt = server.compose_references({"scene": "A cafe.", "subjects": ["a woman in red", "a man in grey"], "dialogue": TWO}, 2)
    head = definitions(prompt)
    assert head.count("<Subject 2> is") == 1 and "shown in <Picture 2>" in head


def test_one_speaker_and_a_picture_is_unchanged():
    one = [{"text": "Good morning.", "voice": "a warm voice"}]
    prompt = server.compose_references({"scene": "A bakery.", "subjects": ["a baker"], "dialogue": one}, 1)
    assert "<Subject 1> (S1), in a warm voice, speaks" in prompt
    assert definitions(prompt).count("<Subject 1> is") == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
