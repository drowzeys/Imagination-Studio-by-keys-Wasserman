"""A take's readable folder and file names (app/take_names.py, issue #257): mechanical, safe on every file system,
the same every time for the same job."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import take_names as names

NOV = time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1))
JAN = time.mktime((2027, 1, 3, 12, 0, 0, 0, 0, -1))


def test_the_title_part_is_the_first_words_made_safe():
    assert names.slug("Heavy footsteps, truck engine idling; then a DOOR slams!") == "heavy-footsteps-truck-engine-idling-then"
    assert names.slug("  Rain   on\ta window ") == "rain-on-a-window"
    assert names.slug("Café déjà vu: l'été") == "cafe-deja-vu-l-ete"  # accents folded, never dropped whole
    assert names.slug("a/b\\c:d*e?f\"g<h>i|j") == "a-b-c-d-e-f"  # no separator or wildcard survives; six words
    assert len(names.slug("supercalifragilisticexpialidocious " * 4)) <= names.LIMIT
    assert names.slug("x" * 200) == "x" * names.LIMIT
    for nothing in ("", "   ", "!!!", "雨の音", None):
        assert names.slug(nothing, "sound-effect") == "sound-effect"
    for device in ("CON", "nul", "Com1", "LPT9"):  # Windows keeps these names for devices
        assert names.slug(device, "foley") == "foley"
    assert names.slug("con artist") == "con-artist"  # only the bare device name is refused
    assert not names.slug("trailing dots... ").endswith((".", " ", "-"))


def test_the_date_and_time_are_the_jobs_own_with_the_year_it_was_queued_in():
    assert names.when("1107-181022-ee3e3a", NOV) == ("2026-11-07", "181022")
    assert names.when("1109-194432-adc366-vsa", NOV) == ("2026-11-09", "194432")
    assert names.when("1228-235959-aaaaaa", JAN) == ("2026-12-28", "235959")  # queued in December, read in January
    assert names.when("0102-000001-bbbbbb", JAN) == ("2027-01-02", "000001")
    day, clock = names.when("j1", NOV)  # another shape of id: today, now
    assert day == "2026-11-15" and len(clock) == 6
    assert names.when("9999-999999-x", NOV)[0] == "2026-11-15"  # not a date: not guessed at


def test_the_token_tells_two_jobs_of_one_second_apart():
    assert names.token("1107-181022-ee3e3a") == "ee3e3a" and names.token("1109-194432-adc366-vsa") == "adc366-vsa"
    assert names.token("j1") == "j1" and names.token("../../etc") == "etc" and names.token("") == "job"


def test_a_take_has_one_stem_for_its_folder_and_files_and_one_place_per_job(tmp_path):
    month, stem = names.stem("1107-181022-ee3e3a", "Heavy footsteps, truck engine", "sound-effect", NOV)
    assert (month, stem) == ("2026-11", "2026-11-07_181022_heavy-footsteps-truck-engine_ee3e3a")
    folder, name = names.folder(tmp_path / "sound" / "sfx", "1107-181022-ee3e3a", "Heavy footsteps, truck engine", "sound-effect", NOV)
    assert folder == tmp_path / "sound/sfx/2026-11" / stem and name == stem
    folder.mkdir(parents=True)
    (folder / f"{name}.wav").write_bytes(b"x")
    # built again for the same job (when it is queued and when it runs): the same place, never "-2"
    assert names.folder(tmp_path / "sound" / "sfx", "1107-181022-ee3e3a", "Heavy footsteps, truck engine", "sound-effect", NOV) == (folder, name)
    # another job of the same second and the same words: another folder
    other, _ = names.folder(tmp_path / "sound" / "sfx", "1107-181022-ff0000", "Heavy footsteps, truck engine", "sound-effect", NOV)
    assert other != folder and other.parent == folder.parent
    # a whole path stays well inside Windows' 260 characters under a usual home
    longest = names.folder(Path("C:/Users/someone/ImaginationStudio/sound/foley"), "1109-194432-adc366-vsa", "w" * 300, "foley", NOV)
    assert len(str(longest[0] / (longest[1] + ".take.json"))) < 230  # Windows' limit is 260


def test_a_take_is_found_by_its_job_id_in_either_layout(tmp_path):
    root = tmp_path / "music"
    folder, name = names.folder(root, "1107-181022-ee3e3a", "Night drive, synthwave", "song", now=NOV)
    folder.mkdir(parents=True)
    (folder / f"{name}.take.json").write_text("{}")
    assert names.find(root, "1107-181022-ee3e3a", ".take.json") == folder / f"{name}.take.json"
    assert names.find(root, "1107-181022-ee3e3a", ".wav") is None and names.find(root, "1107-181022-ffffff", ".take.json") is None
    assert names.find(root, "1107-181023-ee3e3a", ".take.json") is None  # another second is another job
    (root / "1107-181022-ee3e3a.take.json").write_text("{}")  # a take made before the folders wins: it is the id's own name
    assert names.find(root, "1107-181022-ee3e3a", ".take.json") == root / "1107-181022-ee3e3a.take.json"
    # found whatever the year is now: the id carries none
    assert names.find(root, "1107-181022-ee3e3a", ".take.json") is not None
    assert names.find(tmp_path / "nowhere", "1107-181022-ee3e3a", ".wav") is None


def test_a_clip_made_from_a_clip_is_named_by_what_was_done_and_to_what(tmp_path):
    take = "renders/2026-11/2026-11-09_194432_train-through-snow_adc366/2026-11-09_194432_train-through-snow_adc366.mp4"
    assert names.title_of(take) == "train through snow" and names.title_of("renders/c.mp4") == "c" and names.title_of(None) == ""
    made = names.derived(tmp_path, "1109-200000-bbbbbb", "upscale", take)
    assert made.parent.name.endswith("-11-09_200000_upscale-train-through-snow_bbbbbb") and made.name == made.parent.name + ".mp4"
    assert made.parents[2] == tmp_path / "renders"
    hlg, sdr = (names.derived(tmp_path, "1109-200000-bbbbbb", "hlg", take, tag) for tag in ("-hlg", "-sdr"))
    assert hlg.parent == sdr.parent and hlg.name.endswith("_bbbbbb-hlg.mp4") and sdr.name.endswith("_bbbbbb-sdr.mp4")
    assert names.derived(tmp_path, "check", "hlg", take, "-hlg") == tmp_path / "renders/check-hlg.mp4"  # nothing is made for a check
    assert names.derived(tmp_path, "1109-200000-bbbbbb", "title", "").parent.name.endswith("_title_bbbbbb")
