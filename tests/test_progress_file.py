"""SPARK_PROGRESS_FILE (spark/progress_file.py): off by default, two whole lines when set, never in a render's way."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "spark"))
import progress_file as P  # noqa: E402


def fresh(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SPARK_PROGRESS_FILE", raising=False)
    else:
        monkeypatch.setenv("SPARK_PROGRESS_FILE", str(value))
    return importlib.reload(P)


def test_unset_writes_nothing(tmp_path, monkeypatch):
    p = fresh(monkeypatch, None)
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    p.step(3, 8)
    p.done(9.4, "clip.mp4")
    assert list(tmp_path.iterdir()) == []


def test_a_clip_from_start_to_done(tmp_path, monkeypatch):
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    assert target.read_text().splitlines()[0] == "0/8 passes · FastH3 bf16 (ComfyUI) · 864x480 124f"
    p.stage("UNETLoader")
    assert target.read_text().splitlines()[1].startswith("loading the model · ")
    p.stage("NoSuchNode")  # an unknown node keeps the last stage
    assert target.read_text().splitlines()[1].startswith("loading the model")
    p.step(3, 8)
    first, second = target.read_text().splitlines()
    assert first.startswith("3/8 passes · ") and second.startswith("generating · ")
    p.done(9.4, "clip.mp4")
    assert target.read_text().splitlines() == ["8/8 passes · FastH3 bf16 (ComfyUI) · 864x480 124f", "done in 9 s · clip.mp4"]
    assert [f.name for f in tmp_path.iterdir()] == ["progress"]  # no temporary file left beside it


def test_a_failure_says_why(tmp_path, monkeypatch):
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)
    p.begin("H3 Ref2VA (ComfyUI) · 864x480 124f", 8)
    p.failed("this clip needs  more memory\nthan the machine has")
    assert target.read_text().splitlines()[1] == "failed: this clip needs more memory than the machine has"


def test_an_unwritable_file_never_stops_a_render(tmp_path, monkeypatch):
    p = fresh(monkeypatch, tmp_path / "missing-folder" / "progress")
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    p.step(1, 8)
    p.done(1.0)
    p.failed("x")
