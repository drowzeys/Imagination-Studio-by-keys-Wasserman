"""STUDIO_PREVIEW_FILE (spark/job_preview.py): off by default, the newest preview of the job's own prompt kept whole."""

import importlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "spark"))
import job_preview as J  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0preview"


def message(kind: int, image: bytes, prompt_id: str = "p1") -> bytes:
    if kind == 1:
        return struct.pack(">I", 1) + struct.pack(">I", 1) + image
    meta = json.dumps({"node_id": "12", "prompt_id": prompt_id}).encode()
    return struct.pack(">I", 4) + struct.pack(">I", len(meta)) + meta + image


def fresh(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("STUDIO_PREVIEW_FILE", raising=False)
    else:
        monkeypatch.setenv("STUDIO_PREVIEW_FILE", str(value))
    return importlib.reload(J)


def test_unset_asks_for_nothing_and_writes_nothing(tmp_path, monkeypatch):
    j = fresh(monkeypatch, None)
    assert j.extra_data() == {} and not j.wanted()
    assert j.take(message(1, JPEG)) is False and list(tmp_path.iterdir()) == []


def test_both_kinds_of_preview_message_are_read():
    assert J.picture(message(1, JPEG)) == (JPEG, {})
    image, meta = J.picture(message(4, JPEG, "p9"))
    assert image == JPEG and meta["prompt_id"] == "p9"
    assert J.picture(struct.pack(">I", 3) + b"text....") == (None, {}) and J.picture(b"\x00") == (None, {})


def test_set_asks_for_latent2rgb_and_keeps_the_newest_picture_of_its_own_prompt(tmp_path, monkeypatch):
    target = tmp_path / "previews" / "job.jpg"
    j = fresh(monkeypatch, target)
    assert j.extra_data() == {"extra_data": {"preview_method": "latent2rgb"}}
    assert j.take(message(1, JPEG + b"1"), "p1") and target.read_bytes() == JPEG + b"1"
    assert j.take(message(4, JPEG + b"2", "p1"), "p1") and target.read_bytes() == JPEG + b"2"
    assert not j.take(message(4, JPEG + b"3", "other"), "p1") and target.read_bytes() == JPEG + b"2"
    assert not (tmp_path / "previews" / "job.jpg.part").exists()


def test_an_unwritable_file_never_stops_the_render(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    j = fresh(monkeypatch, blocker / "inside" / "job.jpg")  # a parent that is a file
    assert j.take(message(1, JPEG)) is False


def test_the_clip_graph_points_the_preview_at_its_middle_frame_only_when_asked():
    text = (ROOT / "spark" / "spark_generate.py").read_text()
    assert '**({"preview_frame": "middle"} if job_preview.wanted() else {})' in text
    node = (ROOT / "spark" / "tensorfold_h3" / "__init__.py").read_text()
    assert '"preview_frame": (["first", "middle", "last"]' in node and "def preview_at(" in node
