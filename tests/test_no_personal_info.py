"""No real personal information in the repo: no one's email, home folder or tailnet name in any tracked file.

People are reached through GitHub only (Issues, Discussions, private vulnerability reporting; see SECURITY.md), and
commits carry GitHub noreply addresses (checked in CI by the workflow). Examples in code, docs and tests use stand-ins:
example.com / .invalid / .local addresses, /Users/you, /home/spark, tail1234.ts.net."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(@users\.noreply\.github\.com|^noreply@github\.com|^git@github\.com|@example\.(com|org|net)"
                      r"|\.invalid|\.local|\.test|\.localhost)$", re.I)
HOME = re.compile(r"(?:/home/|/Users/)([A-Za-z0-9._-]+)")
HOME_OK = {"you", "me", "someone", "spark", "runner", "user", "name", "shared", "Shared"}
TAILNET = re.compile(r"\b(tail[0-9a-f]+)\.ts\.net\b")
TAILNET_OK = {"tail1234", "tail9999"}


def tracked():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    for name in out.decode().split("\0"):
        path = ROOT / name
        if name and name != "tests/test_no_personal_info.py" and path.is_file() and not path.is_symlink():  # its own samples
            try:
                yield name, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # pictures, sounds, archives


def found():
    for name, text in tracked():
        for number, line in enumerate(text.splitlines(), 1):
            for match in EMAIL.finditer(line):
                if not EMAIL_OK.search(match.group()):
                    yield f"{name}:{number}: email {match.group()}"
            for match in HOME.finditer(line):
                if match.group(1) not in HOME_OK:
                    yield f"{name}:{number}: home folder {match.group()}"
            for match in TAILNET.finditer(line):
                if match.group(1) not in TAILNET_OK:
                    yield f"{name}:{number}: tailnet {match.group()}"


def test_no_personal_information_in_tracked_files():
    assert list(found()) == []


@pytest.mark.parametrize("given, bad", [
    ("write to someone@gmail.com", True), ("Author <a.b@company.co.uk>", True), ("cd /home/alice/studio", True),
    ("https://studio.tail5f3e.ts.net", True), ("1+drowzeys@users.noreply.github.com", False), ("tests@example.invalid", False),
    ("you@your-mac.local", False), ("/Users/you/Movies", False), ("spark.tail1234.ts.net", False), ("git@github.com:x/y", False)])
def test_the_patterns(given, bad, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "tracked", lambda: iter([("x", given)]))
    assert bool(list(found())) is bad
