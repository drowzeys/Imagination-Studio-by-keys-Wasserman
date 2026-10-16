"""The picture models (Qwen 2.1 Turbo, FLUX3-K Fast, FLUX3-K Full): python3 -m pytest tests/test_image_models.py (or
python3 tests/test_image_models.py). No GPU, no ComfyUI, no weights: what a job asks for, and the workflow FLUX3-K
becomes on a Spark."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))


def test_only_installed_models_are_offered_and_asked_for():
    import server
    from fastapi import HTTPException

    had = server.image_models
    try:
        server.image_models = lambda: ["qwen"]
        assert server.image_model_env({}) == {"IMAGE_MODEL": "qwen"}
        for name, said in (("flux3k-fast", "FLUX3-K Fast is not installed"), ("flux9", "unknown picture model")):
            try:
                server.image_model_env({"image_model": name})
                raise AssertionError(name + " was accepted")
            except HTTPException as error:
                assert said in error.detail, error.detail
        server.image_models = lambda: ["qwen", "flux3k-fast", "flux3k-full"]
        assert server.image_model_env({"image_model": "flux3k-full"}) == {"IMAGE_MODEL": "flux3k-full"}
        # a scout job carries the choice to scripts/image.sh
        _, env, _ = server.build("scout", {"prompt": "a lighthouse", "count": 2, "seed": 3, "image_model": "flux3k-fast"}, "j1")
        assert env["IMAGE_MODEL"] == "flux3k-fast"
        labels = [m["label"] for m in server.state()["image_models"]]
        assert labels == ["Qwen 2.1 Turbo", "FLUX3-K Fast", "FLUX3-K Full"], labels
    finally:
        server.image_models = had


def test_the_flux3_workflow_follows_bfl():
    import spark_generate as G

    class Args:
        model, width, height, steps = "flux3k-fast", 1344, 768, None

    with tempfile.TemporaryDirectory() as folder:
        had = G.MODELS
        try:
            G.MODELS = Path(folder)
            (Path(folder) / G.FLUX3_DIR).mkdir()
            (Path(folder) / G.FLUX3_DIR / "system_prompts.json").write_text(json.dumps({"t2i": "You are the prompt upsampler."}))
            w = G.flux3_workflow(Args, "a lighthouse", 7)
            # the upsampler writes the caption inside the workflow, and the text encoder reads it
            assert w["21"]["class_type"] == "TextGenerate" and w["21"]["inputs"]["system_prompt"] == "You are the prompt upsampler."
            assert w["21"]["inputs"]["prompt"] == "a lighthouse" and w["5"]["inputs"]["text"] == ["21", 0]
            assert w["3"]["inputs"]["type"] == "flux3" and w["20"]["inputs"]["clip_name"] == G.FLUX3_UPSAMPLER
            sampler = w["12"]["inputs"]
            assert (sampler["steps"], sampler["cfg"], sampler["seed"]) == (16, 1.0, 7)
            assert w["1"]["inputs"]["unet_name"] == "flux3_image_klein_sd_bf16.safetensors"
            Args.model = "flux3k-full"
            w = G.flux3_workflow(Args, "a lighthouse", 7, caption="Full-color photograph of a lighthouse.")
            assert (w["12"]["inputs"]["steps"], w["12"]["inputs"]["cfg"]) == (50, 3.5)
            assert "21" not in w and w["5"]["inputs"]["text"] == "Full-color photograph of a lighthouse."
        finally:
            G.MODELS = had


def test_an_unknown_model_is_refused_by_the_script():
    if sys.platform.startswith("linux"):
        return  # scripts/image.sh hands over to spark/image.sh, whose argparse refuses it
    done = subprocess.run(["bash", str(ROOT / "scripts/image.sh"), "x", "/tmp/never.png"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "IMAGE_MODEL": "flux9"})
    assert done.returncode == 2 and "IMAGE_MODEL is qwen" in done.stderr


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
