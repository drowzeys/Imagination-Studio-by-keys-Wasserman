"""The Mac's ComfyUI: its model folders (mac/comfy_files.py), the pointer files' names as the runner asks for them, the
renames of mac/studio_files, its installer's pins, and the server script.
Run: python3 -m pytest tests/test_mac_comfy.py (or python3 tests/test_mac_comfy.py)."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mac"))
sys.path.insert(0, str(ROOT / "spark"))
import comfy_files  # noqa: E402


def test_pointer_files_and_the_folder_list():
    with tempfile.TemporaryDirectory() as folder:
        prefix, h3 = Path(folder) / "prefix", Path(folder) / "h3"
        prefix.mkdir()
        os.environ.update(H3_MODEL_DIR=str(h3), MAC_COMFY_MODELS=str(Path(folder) / "models"))
        try:
            assert comfy_files.write(prefix) == {}                      # nothing of MiniMax H3 here: no pointer at all
            for _, part in comfy_files.POINTERS.values():
                (h3 / part).mkdir(parents=True)
            made = comfy_files.write(prefix)
            assert set(made) == set(comfy_files.POINTERS)
            for name, (kind, part) in comfy_files.POINTERS.items():
                text = (Path(folder) / "models" / name).read_text()
                assert text.startswith('{"studio_files"') and json.loads(text)["studio_files"] == {"kind": kind, "folder": str(h3 / part)}
            yaml = (prefix / "comfy-models.yaml").read_text()
            assert f"base_path: {Path(folder) / 'models'}" in yaml and all(f"  {f}: {f}" in yaml for f in comfy_files.FOLDERS)
            (h3 / "FL2VA/video_vae").rmdir()                           # a pointer to nothing is taken away again
            comfy_files.write(prefix)
            assert not (Path(folder) / "models/vae/minimax_h3_video_vae_fp32.studio.sft").exists()
        finally:
            os.environ.pop("MAC_COMFY_MODELS", None)
            os.environ.pop("H3_MODEL_DIR", None)


def test_the_runner_asks_a_mac_for_the_pointer_names():
    import spark_generate as G

    names = {name.rsplit("/", 1)[1] for name in comfy_files.POINTERS}
    assert set(G.MAC_FILES.values()) == names


def studio_files():
    """mac/studio_files with stand-ins for ComfyUI's modules (only its pure functions are tested here)."""

    comfy = types.ModuleType("comfy")
    for sub in ("utils", "sd", "model_patcher"):
        module = types.ModuleType(f"comfy.{sub}")
        setattr(comfy, sub, module)
        sys.modules[f"comfy.{sub}"] = module
    sys.modules["comfy"] = comfy
    comfy.utils.load_torch_file = lambda *a, **k: {}
    comfy.sd.load_diffusion_model = lambda *a, **k: None
    comfy.sd.CLIP = type("CLIP", (), {"load_sd": lambda self, sd, full_model=False: None})
    comfy.model_patcher.ModelPatcher = type("ModelPatcher", (), {"patch_weight_to_device": lambda *a, **k: None,
                                                                 "unpatch_model": lambda *a, **k: None})
    spec = importlib.util.spec_from_file_location("studio_files", ROOT / "mac/studio_files/__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_text_encoder_names_are_comfys_and_cut_after_layer_50():
    try:
        import torch  # noqa: F401
    except ImportError:
        return  # the node needs PyTorch; the CI environment has none
    sf = studio_files()
    assert sf.text_name("model.language_model.layers.0.mlp.up_proj.weight") == "model.layers.0.mlp.up_proj.weight"
    assert sf.text_name("model.language_model.layers.49.input_layernorm.weight") == "model.layers.49.input_layernorm.weight"
    assert sf.text_name("model.language_model.layers.50.input_layernorm.weight") is None
    assert sf.text_name("model.language_model.norm.weight") is None and sf.text_name("lm_head.weight") is None
    assert sf.text_name("model.language_model.embed_tokens.weight") == "model.embed_tokens.weight"
    assert sf.text_name("model.visual.blocks.0.attn.qkv.weight") == "visual.blocks.0.attn.qkv.weight"


def test_fused_qkv_rows_are_regrouped_and_3d_convolution_is_exact():
    try:
        import torch
    except ImportError:
        return
    sf = studio_files()
    heads, dim = 3, sf.HEAD_DIM
    rows = torch.arange(heads * 3 * dim, dtype=torch.float32)[:, None].repeat(1, 2)
    out = sf.dit_weight("blocks.0.attn.qkv_proj.weight", rows)
    # MiniMax's row h*3*128 + j*128 + d (head h, j = q/k/v) is ComfyUI's row j*heads*128 + h*128 + d
    for h in range(heads):
        for j in range(3):
            assert out[j * heads * dim + h * dim, 0] == h * 3 * dim + j * dim
    assert sf.dit_weight("blocks.0.mlp.fc1.weight", rows) is rows
    torch.manual_seed(0)
    for stride, padding in ((1, 0), ((1, 2, 2), 1), (2, (1, 0, 1))):
        x, w, b = torch.randn(1, 4, 7, 10, 12), torch.randn(6, 4, 3, 3, 3), torch.randn(6)
        want, got = sf._conv3d(x, w, b, stride, padding), sf.conv3d_by_taps(x, w, b, stride, padding)
        assert got.shape == want.shape and float((got - want).abs().max()) < 1e-4, (stride, padding)
    assert sf.conv3d(x, w, b) .shape == sf._conv3d(x, w, b).shape        # a CPU tensor goes to PyTorch's own


def test_setup_reads_the_sparks_pins_and_the_server_takes_pytorch_attention():
    setup = (ROOT / "scripts/setup-comfy.sh").read_text()
    assert "oneshot-setup-spark.sh" in setup and "spark/setup-workflows.sh" in setup
    pins = subprocess.run(["bash", "-c", f'HERE="{ROOT}"; ' + re.search(r"^pin\(\) \{.*\}$", setup, re.M).group(0)
                           + "; pin h3-relay; pin ComfyUI-H3-Video-Upsampler"], capture_output=True, text=True).stdout.split()
    assert pins == ["cf489b7f2472fdf33866fae36de49aea03ef464f", "36cb612ec3df30094eeb4fec66f1528925ebba73"]
    server = (ROOT / "mac/comfy.sh").read_text()
    # ComfyUI's sub-quadratic attention on a Mac adds the text mask as a bias (wrong Qwen-Image pictures): never it
    assert "--use-pytorch-cross-attention" in server and "--listen 127.0.0.1" in server and "MAC_COMFY_GUARD_GB" in server
    assert "studio_files" in server and "comfy_files.py" in server
    for option in ("--upscale", "--remove", "--moss"):
        assert option in (ROOT / "oneshot-setup.sh").read_text()


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
