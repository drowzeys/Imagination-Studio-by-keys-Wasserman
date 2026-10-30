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


def test_other_jobs_leave_the_file_alone(tmp_path, monkeypatch):
    # image, music and sound jobs share run(): their sampler and node events come without a clip begun
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)
    p.step(3, 30)
    p.stage("KSampler")
    p.done(2.0, "picture.png")
    p.failed("x")
    assert not target.exists()
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    p.done(9.4, "clip.mp4")
    p.step(5, 30)  # an image job after the clip
    p.stage("VAEDecode")
    assert target.read_text().splitlines() == ["8/8 passes · FastH3 bf16 (ComfyUI) · 864x480 124f", "done in 9 s · clip.mp4"]


def test_any_text_is_written(tmp_path, monkeypatch):
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)
    p.begin("FastH3 · 864x480 124f · café 🎬 \udcff", 8)  # \udcff: a stray surrogate from an odd file name
    assert target.read_text(encoding="utf-8").startswith("0/8 passes · FastH3 · 864x480 124f · café 🎬 ")


def test_a_failed_replace_leaves_no_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)

    def refuse(src, dst):
        raise PermissionError("read-only")

    monkeypatch.setattr(P.os, "replace", refuse)
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    assert list(tmp_path.iterdir()) == []


def test_how_the_process_ended(tmp_path, monkeypatch):
    target = tmp_path / "progress"
    p = fresh(monkeypatch, target)
    for error, said in ((RuntimeError("ComfyUI went away"), "failed: RuntimeError: ComfyUI went away"),
                        (KeyboardInterrupt(), "failed: stopped"), (SystemExit("out of memory"), "failed: out of memory"),
                        (SystemExit(2), "failed: 2")):
        p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
        p.stopped_by(error)
        assert target.read_text().splitlines()[1] == said
    p.begin("FastH3 bf16 (ComfyUI) · 864x480 124f", 8)
    p.done(9.4, "clip.mp4")
    p.stopped_by(SystemExit(0))  # a clean exit after the clip changes nothing
    assert target.read_text().splitlines()[1] == "done in 9 s · clip.mp4"
