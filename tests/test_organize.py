"""Organize existing media (app/organize.py, issue #257): python3 -m pytest tests/test_organize.py. Files only."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import organize  # noqa: E402

MADE = time.mktime((2026, 11, 7, 18, 10, 22, 0, 0, -1))


def write(home: Path, rel: str, text: str = "x") -> str:
    file = home / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    os.utime(file, (MADE, MADE))
    return rel


def older(home: Path) -> None:
    write(home, "voice/1107-181022-aaaaaa.wav")
    write(home, "voice/1107-181022-aaaaaa.txt")
    write(home, "voice/1107-181022-aaaaaa.take.json", json.dumps({"id": "1107-181022-aaaaaa", "path": "voice/1107-181022-aaaaaa.wav",
                                                                     "text": "<|speaker:0|> Hello there."}))
    write(home, "music/1107-181100-bbbbbb.wav")
    write(home, "music/1107-181100-bbbbbb.yue2/score.abc")
    write(home, "music/1107-181100-bbbbbb.take.json", json.dumps({"id": "1107-181100-bbbbbb", "path": "music/1107-181100-bbbbbb.wav",
                                                                     "caption": "Night drive, synthwave", "score": "music/1107-181100-bbbbbb.yue2/score.abc"}))
    write(home, "sound/sfx/1107-181200-cccccc/sound.wav")
    write(home, "sound/sfx/1107-181200-cccccc/sound.take.json", json.dumps({"id": "1107-181200-cccccc", "prompt": "Rain on a window",
                                                                               "path": "sound/sfx/1107-181200-cccccc/sound.wav"}))
    write(home, "sound/sfx/1107-181300-dddddd/audio.json")  # diagnostics only: not a take
    write(home, "scouts/1107-181400-eeeeee/scout_s7.png")
    write(home, "scouts/1107-181400-eeeeee/sheet.jpg")
    write(home, "renders/1107-181500-ffffff.mp4")
    write(home, "renders/1107-181500-ffffff-hlg.mp4")
    write(home, "renders/1107-181600-abcdef.mp4")
    write(home, "renders/1107-181600-abcdef/seg001.mp4")
    write(home, "renders/1107-181700-012345.mp4")  # a film names this one
    write(home, "projects/film.json", json.dumps({"segments": [{"output": "renders/1107-181700-012345.mp4"}]}))
    write(home, "renders/holiday.mp4")  # not a job's file: never touched
    write(home, "voice/2026-11/2026-11-07_190000_new_999999/2026-11-07_190000_new_999999.wav")  # already in its folder


def test_older_takes_move_into_readable_folders_and_what_is_named_elsewhere_stays(tmp_path):
    home = tmp_path
    older(home)
    clips = {"renders/1107-181500-ffffff.mp4": {"title": "Train through snow", "prompt": "A train crosses a bridge"}}
    found = organize.plan(home, clips, busy={"1107-181600-abcdef"})
    assert found["kinds"] == {"voice line": 1, "song": 1, "sound effect": 1, "picture set": 1, "clip": 1} and found["takes"] == 5
    assert {(one["id"], one["why"].split(":")[0]) for one in found["skipped"]} == {
        ("1107-181600-abcdef", "its job is queued or running"), ("1107-181700-012345", "a film, a timeline or another project names it by its path")}
    before = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    done = organize.apply(home, found)
    assert done["takes"] == 5 and done["files"] == found["files"] == 12 and not done["failed"]
    stem = "2026-11-07_181022_hello-there_aaaaaa"
    assert sorted(p.name for p in (home / "voice/2026-11" / stem).iterdir()) == [f"{stem}.take.json", f"{stem}.txt", f"{stem}.wav"]
    assert json.loads((home / "voice/2026-11" / stem / f"{stem}.take.json").read_text())["path"] == f"voice/2026-11/{stem}/{stem}.wav"
    song = "2026-11-07_181100_night-drive-synthwave_bbbbbb"
    said = json.loads((home / "music/2026-11" / song / f"{song}.take.json").read_text())
    assert said["path"] == f"music/2026-11/{song}/{song}.wav" and said["score"] == f"music/2026-11/{song}/{song}.yue2/score.abc"
    assert (home / said["score"]).is_file() and not (home / "music/1107-181100-bbbbbb.yue2").exists()
    rain = "2026-11-07_181200_rain-on-a-window_cccccc"
    assert (home / "sound/sfx/2026-11" / rain / f"{rain}.wav").is_file() and not (home / "sound/sfx/1107-181200-cccccc").exists()
    assert (home / "sound/sfx/1107-181300-dddddd/audio.json").is_file()  # only logs: left
    shots = "2026-11-07_181400_picture_eeeeee"
    assert sorted(p.name for p in (home / "scouts/2026-11" / shots).iterdir()) == [f"{shots}_s7.png", "sheet.jpg"]
    clip = "2026-11-07_181500_train-through-snow_ffffff"
    assert sorted(p.name for p in (home / "renders/2026-11" / clip).iterdir()) == [f"{clip}-hlg.mp4", f"{clip}.mp4"]
    # left where they were: the busy job's clip and its parts, the film's clip, a file that is no job's, a new-layout take
    for kept in ("renders/1107-181600-abcdef.mp4", "renders/1107-181600-abcdef/seg001.mp4", "renders/1107-181700-012345.mp4", "renders/holiday.mp4"):
        assert (home / kept).is_file()
    # a moved path is written down, so it still opens, and a second run finds only what the first left for a reason
    index = organize.moved(home)
    assert index["voice/1107-181022-aaaaaa.wav"] == f"voice/2026-11/{stem}/{stem}.wav" and len(index) == 12
    again = organize.plan(home, clips, busy=set())
    assert again["kinds"] == {"clip": 1} and [one["id"] for one in again["skipped"]] == ["1107-181700-012345"]
    # undo: every file back where it was, the records naming the old paths again, the list emptied
    back = organize.undo(home)
    assert back["files"] == 12 and back["missing"] == [] and back["back"]["voice/1107-181022-aaaaaa.wav"].endswith(f"{stem}.wav")
    assert sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file() and p.parts[len(home.parts)] != "organize") == before
    assert json.loads((home / "voice/1107-181022-aaaaaa.take.json").read_text())["path"] == "voice/1107-181022-aaaaaa.wav"
    assert organize.moved(home) == {} and not (home / "voice/2026-11" / stem).exists() and organize.undo(home) == {"files": 0, "missing": [], "back": {}}


def test_a_take_whose_place_is_taken_is_left_and_a_failed_move_is_put_back(tmp_path, monkeypatch):
    home = tmp_path
    write(home, "voice/1107-181022-aaaaaa.wav")
    write(home, "voice/1107-181022-aaaaaa.take.json", json.dumps({"id": "1107-181022-aaaaaa", "text": "Hello there."}))
    stem = "2026-11-07_181022_hello-there_aaaaaa"
    write(home, f"voice/2026-11/{stem}/{stem}.wav", "another")
    found = organize.plan(home)
    assert found["takes"] == 0 and found["skipped"][0]["why"] == "a file is already at its new place"
    (home / f"voice/2026-11/{stem}/{stem}.wav").unlink()
    found = organize.plan(home)
    real, calls = os.replace, []

    def replace(a, b):
        calls.append(str(a))
        if len(calls) == 2:
            raise PermissionError("locked")
        return real(a, b)
    monkeypatch.setattr(organize.os, "replace", replace)
    done = organize.apply(home, found)
    assert done["takes"] == 0 and done["failed"][0]["id"] == "1107-181022-aaaaaa"
    assert (home / "voice/1107-181022-aaaaaa.wav").is_file() and (home / "voice/1107-181022-aaaaaa.take.json").is_file()  # whole, where it was


def test_a_run_cut_short_is_written_down_take_by_take(tmp_path, monkeypatch):
    home = tmp_path
    older(home)
    found = organize.plan(home)
    saves = []
    real = organize._save

    def save(file, value):
        saves.append((file.name, len(value.get("moved", value))))
        return real(file, value)
    monkeypatch.setattr(organize, "_save", save)
    organize.apply(home, found)
    journal = [n for name, n in saves if name == "last.json"]
    assert journal[:-1] == sorted(journal[:-1]) and len(journal) == found["takes"] + 1 and journal[0] > 0  # after every take
    # the preview wrote nothing: planning twice finds nothing new and no folder was made for it
    assert organize.plan(home)["takes"] == 0
