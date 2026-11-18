"""The small-card Voice route should be selectable and accept a recording without a transcript."""

import sys
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import voice  # noqa: E402


def test_chatterbox_is_selected_on_a_small_linux_install(monkeypatch, tmp_path):
    prefix = tmp_path / "studio"
    python = prefix / "chatterbox-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("python")
    model = prefix / "chatterbox-models/turbo"
    model.mkdir(parents=True)
    for name in ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "ve.safetensors", "conds.pt", "tokenizer_config.json"):
        (model / name).write_text("model")
    monkeypatch.setenv("PREFIX", str(prefix))
    monkeypatch.delenv("VOICE_BACKEND", raising=False)
    monkeypatch.delenv("DRAMA3_BACKEND", raising=False)
    monkeypatch.setattr(voice, "SPARK", True)
    monkeypatch.setattr(voice, "_linux_ram_gb", lambda: 31)
    monkeypatch.setattr(voice, "_backend", None)
    assert voice.backend().name == "chatterbox"


def test_chatterbox_cloning_needs_consent_and_a_long_enough_clip_but_no_transcript(monkeypatch, tmp_path):
    one = voice.Chatterbox()
    monkeypatch.setattr(one, "check", lambda: "")
    monkeypatch.setattr(voice, "_backend", one)
    monkeypatch.setattr(voice, "inside", lambda path: tmp_path / path, raising=False)
    monkeypatch.setattr(voice, "relative", lambda path: path.name, raising=False)
    monkeypatch.setattr(voice, "media_seconds", lambda path: (6.0, None), raising=False)
    (tmp_path / "reference.wav").write_bytes(b"audio")
    body = {"text": "Hello there.", "ref_audio": "reference.wav", "consent": True, "top_k": 1000}
    params = voice.checked(body)
    assert params["ref_text"] == "" and params["ref_audio"] == "reference.wav" and params["top_k"] == 1000
    try:
        voice.checked({**body, "consent": False})
    except HTTPException as error:
        assert "consent" in error.detail
    else:
        raise AssertionError("cloning without consent was accepted")
    monkeypatch.setattr(voice, "media_seconds", lambda path: (4.0, None))
    try:
        voice.checked(body)
    except HTTPException as error:
        assert "5.1" in error.detail
    else:
        raise AssertionError("a reference shorter than five seconds was accepted")
