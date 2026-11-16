"""What is installed in a Windows GPU venv, read from disk (windows_qualification.site_versions):
python3 -m pytest tests/test_windows_qualification_packages.py. No venv and no Windows needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import windows_qualification as qualification  # noqa: E402


def record(site: Path, folder: str, text: str | None, file: str = "METADATA") -> None:
    (site / folder).mkdir(parents=True)
    if text is not None:
        (site / folder / file).write_text(text, encoding="utf-8")


def test_packages_are_read_from_their_own_records_every_time(tmp_path):
    record(tmp_path, "torch-2.14.0+cu134.dist-info", "Metadata-Version: 2.1\nName: torch\nVersion: 2.14.0+cu134\n\nName: not-this\n")
    record(tmp_path, "open_clip_torch-4.2.1.dist-info", "Name: open-clip-torch\nVersion: 4.2.1\n")
    record(tmp_path, "Pillow-12.3.0.dist-info", "Name: Pillow\nVersion: 12.3.0\n")
    record(tmp_path, "old.egg-info", "Name: old.tool\nVersion: 0.1\n", "PKG-INFO")
    record(tmp_path, "~orch-2.13.0.dist-info", "Name: torch\nVersion: 2.13.0\n")  # pip's leftover of an interrupted upgrade
    record(tmp_path, "broken-1.0.dist-info", None)  # no record inside: passed over, never an error (#267)
    record(tmp_path, "nameless-1.0.dist-info", "Version: 1.0\n")
    (tmp_path / "torch").mkdir()
    assert qualification.site_versions(tmp_path) == {"torch": "2.14.0+cu134", "open_clip_torch": "4.2.1", "pillow": "12.3.0", "old_tool": "0.1"}
    # nothing is remembered between calls: a package installed a moment ago is there on the next look
    record(tmp_path, "soxr-1.1.0.dist-info", "Name: soxr\nVersion: 1.1.0\n")
    assert qualification.site_versions(tmp_path)["soxr"] == "1.1.0"


def test_a_folder_that_cannot_be_read_says_so_instead_of_naming_every_package_missing(tmp_path):
    with pytest.raises(OSError):
        qualification.site_versions(tmp_path / "gone")
    import windows_runtime
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"x")
    assert windows_runtime.foley_packages(python) is False  # no site-packages: not installed, no exception
    site = tmp_path / "venv" / "Lib" / "site-packages"
    for name in ("torch", "numpy", "soxr", "librosa", "torchdiffeq", "open_clip_torch"):
        record(site, f"{name}-1.dist-info", f"Name: {name.replace('_', '-')}\nVersion: 1\n")
    assert windows_runtime.foley_packages(python) is True
