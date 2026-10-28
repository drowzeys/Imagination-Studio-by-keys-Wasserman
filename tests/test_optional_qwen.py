"""Qwen-Image-2.1-Turbo is optional where FLUX3-K is installed: oneshot-setup-cuda.sh --verify and --upgrade say so
instead of stopping or warning, no ✓ is printed for weights --upgrade left missing, and scripts/studio-check.py's Qwen
edit checks are n/a without it. python3 -m pytest tests/test_optional_qwen.py"""
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"
QWEN = ["Qwen-Image-2.1-Comfy/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors",
        "Qwen-Image-2.1-Comfy/text_encoders/qwen3vl_8b_int8_convrot.safetensors",
        "Qwen-Image-2.1-Comfy/vae/qwen_image_2.1_vae_bf16.safetensors"]
KLEIN = ["FLUX3-K-Comfy/diffusion_models/flux3-image-klein-bf16.safetensors", "FLUX3-K-Comfy/system_prompts.json"]


def qwen_step(tmp_path, mode, have):
    """The installer's weights helpers and its Qwen-Image-2.1-Turbo step, run on their own with ``have`` under the
    models folder: (exit code, what it printed)."""

    text = (ROOT / "oneshot-setup-cuda.sh").read_text()
    start = text.index("# fetch <repo> <folder under $MODELS>")
    end = text.index("# the end of the Qwen-Image-2.1-Turbo step")
    models = tmp_path / "models"
    for name in have:
        (models / name).parent.mkdir(parents=True, exist_ok=True)
        (models / name).write_bytes(b"x")
    script = (f"set -euo pipefail\nMODELS={models}; PREFIX={tmp_path}/prefix; VERIFY={int(mode == 'verify')}; "
              f"UPGRADE={int(mode == 'upgrade')}\n"
              "say() { printf '==> %s\\n' \"$*\"; }\nok() { printf '  \\342\\234\\223 %s\\n' \"$*\"; }\n"
              "die() { printf '  \\342\\234\\227 %s\\n' \"$*\" >&2; exit 1; }\nelsewhere() { :; }\n" + text[start:end])
    done = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("mode", ["verify", "upgrade"])
def test_a_missing_qwen_beside_flux3k_is_optional(tmp_path, mode):
    code, said = qwen_step(tmp_path, mode, KLEIN)
    assert code == 0 and "- Qwen-Image-2.1-Turbo is not installed (optional: pictures use FLUX3-K" in said
    assert "✓" not in said and "✗" not in said and "! missing" not in said


def test_without_any_picture_model_verify_still_stops(tmp_path):
    code, said = qwen_step(tmp_path, "verify", [])
    assert code == 1 and "✗ missing in" in said and "qwen_image_2.1_turbo_bf16" in said


def test_upgrade_gives_no_tick_for_weights_it_left_missing(tmp_path):
    code, said = qwen_step(tmp_path, "upgrade", [])
    assert code == 0 and "! missing in" in said and "✓" not in said


@pytest.mark.parametrize("mode", ["verify", "upgrade"])
def test_an_installed_qwen_is_ticked(tmp_path, mode):
    code, said = qwen_step(tmp_path, mode, QWEN + KLEIN)
    assert code == 0 and "✓ Qwen-Image-2.1-Turbo (8 steps)" in said and "not installed" not in said


def test_every_tick_after_a_download_waits_for_its_files():
    text = (ROOT / "oneshot-setup-cuda.sh").read_text().splitlines()
    for at, line in enumerate(text):
        if line.strip().startswith("fetch ") and not text[at - 1].rstrip().endswith("\\"):
            follows = next(l.strip() for l in text[at + 1:] if not l.strip().startswith(("fetch ", "text_encoders/", "vae/", "loras/",
                                                                                           "mmaudio_", "#", "[ -f")))
            assert not follows.startswith("ok "), f"line {at + 1}: a ✓ after a download should be got: {follows}"


def load_check():
    spec = importlib.util.spec_from_file_location("studio_check_qwen", ROOT / "scripts/studio-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["c_edit", "c_angles"])
def test_studio_check_calls_qwen_edits_n_a_where_qwen_is_not_installed(name):
    check = load_check()
    run = SimpleNamespace(ctx={"edit_state": {"qwen": False, "models": [{"id": "flux3k-fast"}]},
                               "loras": {"qwen-multiple-angles": {"installed": True}}})
    with pytest.raises(check.Skip, match="Qwen-Image-2.1-Turbo is not installed here"):
        getattr(check, name)(run)


def test_the_qwen_files_checked_are_the_ones_fetched():
    import re
    text = (ROOT / "oneshot-setup-cuda.sh").read_text()
    listed = re.search(r"QWEN_FILES=\(([^)]*)\)", text).group(1).split()
    fetched = re.search(r"fetch Comfy-Org/Qwen-Image-2.1 Qwen-Image-2.1-Comfy (diffusion_models/qwen_image_2.1_turbo[^\n]*\\\n[^\n]*)", text).group(1)
    assert listed == [word for word in fetched.split() if word != "\\"]
