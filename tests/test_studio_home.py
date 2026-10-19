"""Where the studio keeps everything (app/studio_home.py): python3 -m pytest tests/test_studio_home.py."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import studio_home as module  # noqa: E402
from studio_home import studio_home  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_other_server(monkeypatch):
    # a studio running on the machine that runs the tests must not change what they find
    monkeypatch.setattr(module, "server_running", lambda: False)


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


def test_importing_the_server_never_moves_the_folder(tmp_path):
    # only a started studio moves it: a script, a test or a check that imports server.py leaves the person's folder be
    (tmp_path / "TensorFoldStudio").mkdir()
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_HOME"}
    env["HOME"] = str(tmp_path)
    app = Path(__file__).resolve().parent.parent / "app"
    done = subprocess.run([sys.executable, "-c", "import server; print(server.HOME)"], cwd=app, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
    assert done.stdout.strip().endswith("TensorFoldStudio") and not (tmp_path / "TensorFoldStudio").is_symlink()
    assert not (tmp_path / "ImaginationStudio").exists()


def test_a_linked_old_folder_is_the_home_and_is_never_moved(tmp_path, monkeypatch):
    # ~/TensorFoldStudio as a link to a larger disk or a share: that place stays the home, nothing empty is made
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    (tmp_path / "big-disk/studio/renders").mkdir(parents=True)
    (tmp_path / "TensorFoldStudio").symlink_to(tmp_path / "big-disk/studio", target_is_directory=True)
    assert studio_home(move=True) == tmp_path / "TensorFoldStudio"
    assert not (tmp_path / "ImaginationStudio").exists() and (tmp_path / "TensorFoldStudio").is_symlink()
    assert (tmp_path / "big-disk/studio/renders").is_dir()


def test_a_running_studio_keeps_its_folder_where_it_is(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    monkeypatch.setattr(module, "server_running", lambda: True)
    (tmp_path / "TensorFoldStudio").mkdir()
    assert studio_home(move=True) == tmp_path / "TensorFoldStudio" and not (tmp_path / "ImaginationStudio").exists()
    assert "a studio server is running" in capsys.readouterr().err


def test_a_move_that_fails_is_said(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STUDIO_HOME", raising=False)
    (tmp_path / "TensorFoldStudio").mkdir()
    def refuse(self, target):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "rename", refuse)
    assert studio_home(move=True) == tmp_path / "TensorFoldStudio"
    assert "could not be moved" in capsys.readouterr().err


def test_the_scripts_ask_the_same_helper(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_HOME"}
    env["HOME"] = str(tmp_path)
    (tmp_path / "big").mkdir()
    (tmp_path / "TensorFoldStudio").symlink_to(tmp_path / "big", target_is_directory=True)
    app = Path(__file__).resolve().parent.parent / "app"
    done = subprocess.run([sys.executable, str(app / "studio_home.py")], env=env, capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == str(tmp_path / "TensorFoldStudio")
    for script in ("scripts/turnaround.sh", "oneshot-essential-bundle.sh", "scripts/lora-bundle.py"):
        text = (app.parent / script).read_text()
        assert "studio_home" in text and "/TensorFoldStudio" not in text and '("TensorFoldStudio"' not in text, script
