"""Where the studio keeps everything (app/studio_home.py): python3 -m pytest tests/test_studio_home.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from studio_home import studio_home  # noqa: E402


def test_a_new_install_uses_the_folder_named_after_the_studio(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    assert studio_home(move=True) == tmp_path / "ImaginationStudio"


def test_an_older_folder_is_moved_once_and_its_old_name_still_finds_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    (tmp_path / "TensorFoldStudio/renders").mkdir(parents=True)
    (tmp_path / "TensorFoldStudio/renders/take.mp4").write_bytes(b"clip")
    assert studio_home() == tmp_path / "TensorFoldStudio"  # only asked: nothing moves
    assert studio_home(move=True) == tmp_path / "ImaginationStudio"
    assert (tmp_path / "ImaginationStudio/renders/take.mp4").read_bytes() == b"clip"
    assert (tmp_path / "TensorFoldStudio").is_symlink() and (tmp_path / "TensorFoldStudio/renders/take.mp4").read_bytes() == b"clip"
    assert studio_home(move=True) == tmp_path / "ImaginationStudio"  # the second start finds it in place


def test_studio_home_set_by_hand_is_used_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "TensorFoldStudio").mkdir()
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "my-dev-home"))
    assert studio_home(move=True) == tmp_path / "my-dev-home" and not (tmp_path / "ImaginationStudio").exists()


def test_both_folders_there_leaves_the_old_one_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    (tmp_path / "TensorFoldStudio").mkdir()
    (tmp_path / "ImaginationStudio").mkdir()
    assert studio_home(move=True) == tmp_path / "ImaginationStudio" and not (tmp_path / "TensorFoldStudio").is_symlink()
