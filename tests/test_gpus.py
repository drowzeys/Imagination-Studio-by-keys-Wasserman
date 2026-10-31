"""spark/gpus.py: a workflow's text encoders and decoders placed on the second card when there are two."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "spark"))
import gpus  # noqa: E402

TWO = [{"index": 0, "name": "card", "total_mb": 48000, "used_mb": 0, "free_mb": 48000},
       {"index": 1, "name": "card", "total_mb": 48000, "used_mb": 0, "free_mb": 48000}]


def video_workflow():
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "fasth3.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "te.safetensors", "type": "minimax", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "video.safetensors"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "audio.safetensors"}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": "x"}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["5", 0]}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"latent_image": ["6", 1]}},
    }


def with_cards(monkeypatch, found, split="auto"):
    monkeypatch.setattr(gpus, "cards", lambda fresh=False: found)
    monkeypatch.setenv("STUDIO_GPU_SPLIT", split)
    monkeypatch.delenv("STUDIO_VRAM_GB", raising=False)
    monkeypatch.delenv("STUDIO_ENCODER_GPU", raising=False)
    monkeypatch.delenv("STUDIO_DECODER_GPU", raising=False)


def test_one_card_leaves_the_workflow_alone(monkeypatch):
    with_cards(monkeypatch, TWO[:1])
    w = video_workflow()
    assert gpus.place(w) == w


def test_split_off_leaves_the_workflow_alone(monkeypatch):
    with_cards(monkeypatch, TWO, split="off")
    w = video_workflow()
    assert gpus.place(w) == w


def test_two_cards_route_encoder_and_decoders_to_the_second(monkeypatch):
    with_cards(monkeypatch, TWO)
    w = video_workflow()
    out = gpus.place(w)
    selects = {key: node for key, node in out.items() if node["class_type"].startswith("Select")}
    assert len(selects) == 3
    clip = [k for k, n in selects.items() if n["class_type"] == "SelectCLIPDevice"]
    vaes = {n["inputs"]["vae"][0]: k for k, n in selects.items() if n["class_type"] == "SelectVAEDevice"}
    assert len(clip) == 1 and out[clip[0]]["inputs"] == {"clip": ["3", 0], "device": "gpu:1"}
    assert set(vaes) == {"4", "5"} and all(selects[k]["inputs"]["device"] == "gpu:1" for k in vaes.values())
    # every consumer reads the placed output, not the loader's
    assert out["6"]["inputs"]["clip"] == [clip[0], 0]
    assert out["6"]["inputs"]["vae"] == [vaes["4"], 0]
    assert out["13"]["inputs"]["vae"] == [vaes["4"], 0]
    assert out["14"]["inputs"]["vae"] == [vaes["5"], 0]
    # the model and the other links are untouched, and the input is not changed in place
    assert out["12"]["inputs"]["latent_image"] == ["6", 1]
    assert not any(n["class_type"] == "SelectModelDevice" for n in out.values())
    assert w == video_workflow()


def test_devices_from_the_environment(monkeypatch):
    with_cards(monkeypatch, TWO)
    monkeypatch.setenv("STUDIO_ENCODER_GPU", "1")
    monkeypatch.setenv("STUDIO_DECODER_GPU", "0")
    out = gpus.place(video_workflow())
    devices = {n["class_type"]: n["inputs"]["device"] for n in out.values() if n["class_type"].startswith("Select")}
    assert devices == {"SelectCLIPDevice": "gpu:1", "SelectVAEDevice": "gpu:0"}


def test_checkpoint_loader_clip_and_vae_outputs(monkeypatch):
    with_cards(monkeypatch, TWO)
    w = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
         "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a"}},
         "3": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}},
         "4": {"class_type": "VAEDecode", "inputs": {"vae": ["1", 2], "samples": ["3", 0]}}}
    out = gpus.place(w)
    assert out["3"]["inputs"]["model"] == ["1", 0]
    assert out[out["2"]["inputs"]["clip"][0]]["class_type"] == "SelectCLIPDevice"
    assert out[out["4"]["inputs"]["vae"][0]]["class_type"] == "SelectVAEDevice"


def test_a_workflow_that_places_itself_is_not_placed_twice(monkeypatch):
    with_cards(monkeypatch, TWO)
    w = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "te"}},
         "2": {"class_type": "SelectCLIPDevice", "inputs": {"clip": ["1", 0], "device": "gpu:0"}},
         "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": "a"}}}
    assert gpus.place(w) == w


def bf16_video_workflow(engine=False):
    w = video_workflow()
    w["1"]["inputs"]["unet_name"] = "fastvideo_fasth3_8step_v2_pruned_bf16.safetensors"
    w["2"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["1", 0]}}
    w["30"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": "x"}}
    last = ["30", 0]
    if engine:
        w["22"] = {"class_type": "TensorFoldH3Blocks", "inputs": {"model": last, "unet_name": "x", "sparsity": 0.8}}
        last = ["22", 0]
    w["10"] = {"class_type": "BasicScheduler", "inputs": {"model": last, "steps": 8}}
    w["11"] = {"class_type": "CFGGuider", "inputs": {"model": last, "positive": ["6", 0]}}
    return w


def test_the_layer_split_is_off_unless_asked_for(monkeypatch):
    with_cards(monkeypatch, TWO)
    monkeypatch.delenv("STUDIO_LAYER_SPLIT", raising=False)
    assert not any(n["class_type"] == "StudioSplitH3Blocks" for n in gpus.place(bf16_video_workflow()).values())


def test_a_bf16_h3_transformer_is_split_after_its_lora(monkeypatch):
    with_cards(monkeypatch, TWO)
    monkeypatch.setenv("STUDIO_LAYER_SPLIT", "on")
    monkeypatch.setenv("STUDIO_SPLIT_BLOCK", "20")
    out = gpus.place(bf16_video_workflow())
    splits = {k: n for k, n in out.items() if n["class_type"] == "StudioSplitH3Blocks"}
    assert len(splits) == 1
    (key, node), = splits.items()
    assert node["inputs"]["model"] == ["30", 0] and node["inputs"]["split"] == 20 and node["inputs"]["device"] == "gpu:1"
    assert node["inputs"]["unet_name"] == "fastvideo_fasth3_8step_v2_pruned_bf16.safetensors"
    assert out["10"]["inputs"]["model"] == [key, 0] and out["11"]["inputs"]["model"] == [key, 0]
    assert out["30"]["inputs"]["model"] == ["2", 0]  # the steps before it are untouched


def test_no_layer_split_on_the_int8_engine_an_int8_file_or_when_off(monkeypatch):
    with_cards(monkeypatch, TWO)
    monkeypatch.setenv("STUDIO_LAYER_SPLIT", "on")
    assert not any(n["class_type"] == "StudioSplitH3Blocks" for n in gpus.place(bf16_video_workflow(engine=True)).values())
    w = bf16_video_workflow()
    w["1"]["inputs"]["unet_name"] = "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
    assert not any(n["class_type"] == "StudioSplitH3Blocks" for n in gpus.place(w).values())
    monkeypatch.setenv("STUDIO_LAYER_SPLIT", "off")
    assert not any(n["class_type"] == "StudioSplitH3Blocks" for n in gpus.place(bf16_video_workflow()).values())


def test_the_simulation_of_one_card_never_splits(monkeypatch):
    with_cards(monkeypatch, TWO)
    monkeypatch.setenv("STUDIO_VRAM_GB", "96")
    assert not gpus.split()


SMI = ("0, NVIDIA RTX PRO 5000 Blackwell, 48935, 17, 48918, GPU-aaaaaaaa-0000-0000-0000-000000000000\n"
       "1, NVIDIA RTX PRO 5000 Blackwell, 48935, 17, 48918, GPU-bbbbbbbb-0000-0000-0000-000000000000\n")


def smi_cards(monkeypatch, visible):
    monkeypatch.setattr(gpus, "_CARDS", {"at": 0.0, "value": []})
    monkeypatch.setattr(gpus.subprocess, "run", lambda *a, **k: type("Done", (), {"returncode": 0, "stdout": SMI})())
    if visible is None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    else:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", visible)
    return [card["index"] for card in gpus.cards(fresh=True)]


def test_the_cards_are_the_ones_this_process_was_pinned_to(monkeypatch):
    # 4.3's STUDIO_GPU pins one card: nothing to split
    assert smi_cards(monkeypatch, "0") == [0]
    # STUDIO_GPUS=0,1 (spark/gpu.sh): both, the head's first; 1,0 puts card 1 first (CUDA's device 0)
    assert smi_cards(monkeypatch, "0,1") == [0, 1]
    assert smi_cards(monkeypatch, "1,0") == [1, 0]
    # a managed local worker is pinned by UUID: its own card only, so it never splits onto the head's
    assert smi_cards(monkeypatch, "GPU-bbbbbbbb-0000-0000-0000-000000000000") == [1]
    # nothing pinned: every card
    assert smi_cards(monkeypatch, None) == [0, 1]


def test_a_local_worker_never_splits(monkeypatch):
    monkeypatch.delenv("STUDIO_GPU_SPLIT", raising=False)
    monkeypatch.delenv("STUDIO_VRAM_GB", raising=False)
    smi_cards(monkeypatch, "GPU-aaaaaaaa-0000-0000-0000-000000000000")
    assert not gpus.split()
    w = video_workflow()
    assert gpus.place(w) == w
