"""Uninstall plans and safety checks use synthetic weight files only."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import windows_model_remove as removal


@pytest.fixture
def models(tmp_path, monkeypatch):
    monkeypatch.setattr(removal.model_catalog, "catalogue", lambda: {"models": {
        "first": {}, "second": {}}})
    manifests = {
        "first": {Path("First/weights.safetensors"), Path("Shared/encoder.safetensors"), Path("First/config.json")},
        "second": {Path("Second/weights.safetensors"), Path("Shared/encoder.safetensors")}}
    monkeypatch.setattr(removal, "manifest", lambda key: manifests[key])
    for name in set.union(*manifests.values()):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test")
    monkeypatch.setattr(removal, "require_stopped", lambda: None)
    monkeypatch.setenv("STUDIO_GPU_LOCK", str(tmp_path / "lock"))
    return tmp_path


def test_preview_preserves_shared_weights_metadata_and_other_models(models):
    plan = removal.plan("first", models)
    assert [Path(row["path"]).name for row in plan["files"]] == ["weights.safetensors"]
    assert plan["shared_files_preserved"] == 1
    removal.remove("first", models, plan)
    assert not (models / "First/weights.safetensors").exists()
    assert (models / "First/config.json").exists()
    assert (models / "Shared/encoder.safetensors").exists()
    assert (models / "Second/weights.safetensors").exists()


def test_changed_files_refuse_removal_before_any_delete(models):
    plan = removal.plan("first", models)
    target = models / "First/weights.safetensors"
    target.write_text("changed")
    with pytest.raises(RuntimeError, match="changed after"):
        removal.remove("first", models, plan)
    assert target.exists()


def test_held_gpu_lock_refuses_removal(models):
    (models / "lock").mkdir()
    with pytest.raises(RuntimeError, match="GPU lock"):
        removal.remove("first", models, removal.plan("first", models))
    assert (models / "First/weights.safetensors").exists()


def test_no_confirmation_or_plan_only_never_removes(models, monkeypatch, capsys):
    monkeypatch.setattr(removal, "remove", lambda *args: pytest.fail("No delete expected"))
    assert removal.main(["--model", "first", "--models", str(models), "--plan-only"]) == 0
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    assert removal.main(["--model", "first", "--models", str(models)]) == 0
    assert "Cancelled" in capsys.readouterr().out


def test_hardlinked_weights_are_not_removed(models):
    import os
    os.link(models / "First/weights.safetensors", models / "alias.safetensors")
    with pytest.raises(ValueError, match="hard-linked"):
        removal.plan("first", models)


def test_escaping_catalog_paths_are_refused(models, monkeypatch):
    monkeypatch.setattr(removal, "manifest", lambda key: {Path("../outside.safetensors")} if key == "first" else set())
    with pytest.raises(ValueError, match="escapes"):
        removal.plan("first", models)


def test_active_or_unreachable_unknown_service_fails_closed(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(removal.socket, "create_connection", lambda *args, **kwargs: nullcontext())
    with pytest.raises(RuntimeError, match="Stop Studio"):
        removal.require_stopped()
    def failed(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(removal.socket, "create_connection", failed)
    with pytest.raises(RuntimeError, match="Could not establish"):
        removal.require_stopped()


def test_actual_catalog_qwen_variants_preserve_shared_encoder_and_vae():
    first = removal.manifest("qwen")
    second = removal.manifest("qwen-full")
    assert any("text_encoders" in path.parts for path in first & second)
    assert any("vae" in path.parts for path in first & second)
    assert {path.name for path in first - second if path.suffix == ".safetensors"} == {
        "qwen_image_2.1_turbo_bf16.safetensors"}
