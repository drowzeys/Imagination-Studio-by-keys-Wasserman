"""Hidden venv command selection without launching processes or probing hardware."""
from pathlib import Path

import pytest

from windows_process import hidden_venv_command


def test_hidden_venv_uses_windowless_redirector_preserving_arguments(tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    python = scripts / "python.exe"
    pythonw = scripts / "pythonw.exe"
    pythonw.touch()
    command = [str(python), "-X", "utf8", "worker.py", "--serve"]
    assert hidden_venv_command(command, 0x08000004) == [str(pythonw), *command[1:]]
    assert command[0] == str(python)
    assert hidden_venv_command(command, 0x200) is command


def test_hidden_venv_missing_windowless_launcher_reports_repair(tmp_path):
    with pytest.raises(FileNotFoundError, match="repair"):
        hidden_venv_command([str(tmp_path / "Scripts" / "python.exe"), "worker.py"], 0x08000000)


@pytest.mark.parametrize("command", [["python", "worker.py"], ["tool.exe"], "python worker.py", []])
def test_other_commands_are_unchanged(command):
    assert hidden_venv_command(command, 0x08000000) is command
