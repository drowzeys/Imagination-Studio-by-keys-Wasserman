"""Model metadata contracts: one source for setup, runtime admission, sizes and UI."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import model_catalog as catalog
import windows_setup_fit as fit


def test_admission_and_planner_share_the_same_catalog_values():
    import voice
    import music
    import bolt
    assert voice.Cuda.NEED_GB == catalog.required_free_gib("voice") == fit.requirement("voice")["required_mb"] / 1024
    assert bolt.Cuda.NEED_GB == catalog.required_free_gib("bolt") == fit.requirement("bolt")["required_mb"] / 1024
    assert music.Cuda.NEED_OF == {key: catalog.required_free_gib("music", key) for key in ("int8", "bf16")}


def test_downloader_image_picker_and_setup_share_model_file_metadata():
    import windows_runtime
    import windows_image_setup
    assert windows_runtime.IMAGE_SPECS == catalog.image_specs()
    spec = importlib.util.spec_from_file_location("catalog_downloader", ROOT / "scripts" / "windows-cuda-setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PARTS == catalog.parts()
    for model in windows_runtime.IMAGE_SPECS:
        rows = windows_image_setup.model_files(model)
        assert rows == [(row["repo"], row["folder"], row["files"]) for row in catalog.download_rows(model)]
        transformers = [name for _, _, names in rows for name in names if name.startswith("diffusion_models/")]
        assert transformers == ["diffusion_models/" + catalog.model(model)["transformer"]]


@pytest.mark.parametrize("model", ["qwen", "qwen-full", "flux3k-fast", "flux3k-full", "moss-image",
                                  "voice", "music", "bolt", "foley", "train", "swap", "workflows"])
def test_download_size_is_sum_of_selected_published_file_sizes(model):
    sizes = catalog.catalogue()["file_sizes"]
    total = 0
    for row in catalog.download_rows(model):
        source = sizes[row["repo"] + "@" + row["revision"]]
        names = row["files"] if row["files"] is not None else source["snapshot_files"]
        total += sum(source["files"][name] for name in names)
    result = catalog.size_info(model)
    assert result["bytes"] == total and total > 0
    assert result["known_files"] == result["total_files"]
    assert result["checked_at"] and result["basis"] == "Hugging Face repository file metadata"


def test_new_catalog_entry_gets_fit_and_download_info_without_planner_changes(monkeypatch):
    data = copy.deepcopy(catalog.catalogue())
    data["models"]["example-model"] = {
        "label": "Example model", "part": "example-part", "kind": "component",
        "runtime": {"cuda": {"required_free_gib": 40, "basis": "policy", "note": "Test policy"}}}
    data["downloads"]["example-part"] = [{"repo": "example/model", "folder": "Example", "files": ["weights.safetensors"]}]
    data["file_sizes"]["example/model@main"] = {"files": {"weights.safetensors": 1234567890}}
    monkeypatch.setattr(catalog, "catalogue", lambda: data)
    result = fit.model_info("example-model", hardware={"gpu_total_mb": 31232, "n1x": True})
    assert result["runtime_fit"]["state"] == "insufficient_capacity"
    assert result["download_size"]["bytes"] == 1234567890
    assert fit.requirement("example-part")["required_mb"] == 40960


def test_missing_size_metadata_is_not_fabricated(monkeypatch):
    data = copy.deepcopy(catalog.catalogue())
    data["file_sizes"] = {}
    monkeypatch.setattr(catalog, "catalogue", lambda: data)
    assert catalog.size_info("voice")["bytes"] is None
    assert catalog.size_info("qwen")["bytes"] is None


def test_optional_checksum_pins_remain_shared_with_the_downloader():
    spec = importlib.util.spec_from_file_location("catalog_checked_downloader", ROOT / "scripts" / "windows-cuda-setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CHECKED == catalog.checked_downloads()
    assert all(len(row) == 6 for rows in module.CHECKED.values() for row in rows)


def test_library_component_parts_reuse_existing_shared_files_without_fasth3_download():
    shared = catalog.parts()["h3-shared"]
    assert len(shared)==1 and len(shared[0][2])==3
    assert all(not name.startswith("diffusion_models/") for name in shared[0][2])
    transformer=catalog.parts()["library-h3"]
    assert transformer[0][2]==["diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors"]
    assert catalog.size_info("h3-shared")["bytes"] > 0
    assert catalog.size_info("library-h3")["bytes"] > 0


def test_catalog_loader_rejects_invalid_policy(tmp_path, monkeypatch):
    data = copy.deepcopy(catalog.catalogue())
    data["models"]["voice"]["runtime"]["cuda"]["required_free_gib"] = -1
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(catalog, "PATH", path)
    catalog.catalogue.cache_clear()
    try:
        with pytest.raises(ValueError, match="Invalid runtime memory"):
            catalog.catalogue()
    finally:
        catalog.catalogue.cache_clear()
