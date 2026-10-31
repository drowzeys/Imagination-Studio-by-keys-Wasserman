"""Two (or more) discrete NVIDIA cards working as one Studio: the stages of a ComfyUI workflow placed per card.

A DGX Spark has one GPU with the machine's memory; an RTX Pro 6000 has one 96 GB card. A workstation with two smaller
cards (2 x 48 GB RTX PRO 5000 Blackwell, for instance) holds no video model and its text encoder together on either
card, but each stage of a render fits on one: the transformer (FastH3, 22 GB int8 or 44 GB bf16) on the first card, the
text encoder (H3's 32B, 8-bit) and the video and audio decoders on the second. ComfyUI (v0.42+) carries the nodes that
do this (Select CLIP Device, Select VAE Device: comfy_extras/nodes_multigpu.py); ``place`` puts them into a workflow
just before it is queued, so every workflow the Studio builds (pictures, clips, references, music, sound, the Library's
actions) is placed the same way without each one knowing about it.

Environment:
  STUDIO_GPUS         the cards one Studio renders on together, nvidia-smi indexes with the head's card first (0,1);
                      spark/gpu.sh pins CUDA to them. Without it, 4.3's STUDIO_GPU pins one card and nothing is split
  STUDIO_GPU_SPLIT    auto (default: on with two or more visible discrete cards), on, off
  STUDIO_ENCODER_GPU  the card (ComfyUI's gpu:N index among the visible ones) for text encoders and decoders, default 1
  STUDIO_DECODER_GPU  the card for the decoders (VAEs) when it should differ from the encoders', default the same
  STUDIO_LAYER_SPLIT  off (default), on: MiniMax H3's bf16 transformer blocks from
                      STUDIO_SPLIT_BLOCK (default 25) on run on the second card (spark/studio_split_h3)
  STUDIO_LAYER_GPU    the card for those blocks, default STUDIO_ENCODER_GPU
"""

from __future__ import annotations

import os
import subprocess
import time

# loaders whose outputs are (output index -> what it is); the model outputs stay on ComfyUI's default device (gpu:0)
CLIP_OUTPUTS = {"CLIPLoader": (0,), "DualCLIPLoader": (0,), "TripleCLIPLoader": (0,), "QuadrupleCLIPLoader": (0,),
                "CheckpointLoaderSimple": (1,), "ImageOnlyCheckpointLoader": ()}
VAE_OUTPUTS = {"VAELoader": (0,), "CheckpointLoaderSimple": (2,), "ImageOnlyCheckpointLoader": (2,)}
_CARDS: dict = {"at": 0.0, "value": []}
# nodes that take a MODEL and give one back (the layer split goes after them, so it sees their patches)
MODEL_STEPS = {"UNETLoader", "MiniMaxH3SigmaShift", "LoraLoaderModelOnly", "LoraLoader", "BlockSparseAttention",
               "SelectModelDevice", "StudioSplitH3Blocks", "TensorFoldH3Blocks", "ModelSamplingSD3", "ModelSamplingFlux"}


def cards(fresh: bool = False) -> list[dict]:
    """Every NVIDIA GPU with memory of its own: [{"index", "name", "total_mb", "used_mb", "free_mb"}]. Empty on a
    DGX Spark (its GB10 has no memory of its own: nvidia-smi says [N/A]) or without nvidia-smi. At most every 2 s."""

    if not fresh and time.monotonic() - _CARDS["at"] < 2:
        return _CARDS["value"]
    found = []
    try:
        said = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free,uuid",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        lines = said.stdout.splitlines() if said.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        lines = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 5 and all(part.isdigit() for part in (parts[0], *parts[2:5])):
            found.append({"index": int(parts[0]), "name": parts[1], "total_mb": int(parts[2]),
                          "used_mb": int(parts[3]), "free_mb": int(parts[4]),
                          "uuid": parts[5] if len(parts) > 5 else ""})
    # the cards this process can use, in CUDA's order (spark/gpu.sh pins them: one card, a local worker's UUID, or
    # paired cards with the head's first); nvidia-smi itself ignores CUDA_VISIBLE_DEVICES
    visible = [part.strip() for part in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if part.strip()]
    if visible:
        by_key = {**{str(card["index"]): card for card in found}, **{card["uuid"]: card for card in found if card["uuid"]}}
        found = [by_key[key] for key in visible if key in by_key]
    _CARDS.update(at=time.monotonic(), value=found)
    return found


def split() -> bool:
    """Whether a workflow's stages are spread over the cards (STUDIO_GPU_SPLIT; auto: two or more discrete cards)."""

    wanted = os.environ.get("STUDIO_GPU_SPLIT", "auto").strip().lower()
    if wanted in ("0", "off", "no", "false"):
        return False
    if os.environ.get("STUDIO_VRAM_GB", "").strip():
        return False  # the one-card simulation on a Spark
    return len(cards()) >= 2  # "on" and "auto" alike: there is nothing to split on one card


def encoder_gpu() -> int:
    try:
        return int(os.environ.get("STUDIO_ENCODER_GPU", "1"))
    except ValueError:
        return 1


def decoder_gpu() -> int:
    try:
        return int(os.environ.get("STUDIO_DECODER_GPU", str(encoder_gpu())))
    except ValueError:
        return encoder_gpu()


def place(workflow: dict) -> dict:
    """The workflow with each text encoder and decoder routed to its card (a Select CLIP / VAE Device node after
    its loader, every use of the loader's output pointed at it); unchanged when the cards are not split. A workflow
    that already places a loader itself is left as it is for that loader."""

    if not split():
        return workflow
    out = {key: dict(node, inputs=dict(node.get("inputs", {}))) for key, node in workflow.items()}
    taken = {int(key) for key in out if str(key).isdigit()}
    next_id = max(taken, default=0) + 1000
    placed = {(ref[0], ref[1]) for node in out.values() if node.get("class_type") in ("SelectCLIPDevice", "SelectVAEDevice")
              for ref in node["inputs"].values() if isinstance(ref, list) and len(ref) == 2}
    routes = {}  # (loader id, output) -> new node id
    for key, node in list(out.items()):
        kind = str(node.get("class_type", ""))
        for slot in CLIP_OUTPUTS.get(kind, ()):
            if (key, slot) not in placed:
                routes[(key, slot)] = ("SelectCLIPDevice", "clip", f"gpu:{encoder_gpu()}")
        for slot in VAE_OUTPUTS.get(kind, ()):
            if (key, slot) not in placed:
                routes[(key, slot)] = ("SelectVAEDevice", "vae", f"gpu:{decoder_gpu()}")
    if not routes:
        return place_decode(place_layers(out) if layer_split() else out)
    new_ids = {}
    for (key, slot), (kind, field, device) in routes.items():
        new_id = str(next_id)
        next_id += 1
        new_ids[(key, slot)] = new_id
        out[new_id] = {"class_type": kind, "inputs": {field: [key, slot], "device": device}}
    for node_id, node in out.items():
        if node_id in new_ids.values():
            continue
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and (str(value[0]), value[1]) in new_ids:
                node["inputs"][name] = [new_ids[(str(value[0]), value[1])], 0]
    return place_decode(place_layers(out) if layer_split() else out)


def decode_split() -> bool:
    """Whether a MiniMax H3 video decode runs on two cards (STUDIO_DECODE_SPLIT, on by default with two cards)."""

    wanted = os.environ.get("STUDIO_DECODE_SPLIT", "on").strip().lower()
    return wanted not in ("0", "off", "no", "false") and split()


def place_decode(out: dict) -> dict:
    """Each VAEDecode of the MiniMax H3 video VAE on the decoder's card becomes StudioTwoCardVAEDecode
    (spark/tensorfold_h3/decode2.py) with a second copy of the VAE on another card: the decoder's temporal chunks
    alternate between the two, blended in order on the decoder's card (byte for byte the one-card output)."""

    if not decode_split():
        return out
    home = f"gpu:{decoder_gpu()}"
    others = [index for index in range(len(cards())) if index != decoder_gpu()]
    if not others:
        return out
    next_id = max((int(key) for key in out if str(key).isdigit()), default=0) + 1
    for key, node in list(out.items()):
        if node.get("class_type") != "VAEDecode":
            continue
        ref = node["inputs"].get("vae")
        select = out.get(str(ref[0])) if isinstance(ref, list) and len(ref) == 2 else None
        if not select or select.get("class_type") != "SelectVAEDevice" or select["inputs"].get("device") != home:
            continue
        source = select["inputs"].get("vae")
        loader = out.get(str(source[0])) if isinstance(source, list) and len(source) == 2 else None
        if not loader or loader.get("class_type") != "VAELoader" or "h3_video_vae" not in str(loader["inputs"].get("vae_name", "")):
            continue
        twin = str(next_id)
        next_id += 1
        out[twin] = {"class_type": "SelectVAEDevice", "inputs": {"vae": list(source), "device": f"gpu:{others[0]}"}}
        out[key] = {"class_type": "StudioTwoCardVAEDecode", "inputs": {**node["inputs"], "vae_other": [twin, 0]}}
    return out


def layer_split() -> bool:
    """Off unless asked for (STUDIO_LAYER_SPLIT=on): measured slower than one card on plain clips, and with sparse
    attention (1344x768 and up) its clip differs from the unsplit one's (PSNR 14 dB, same seed), not yet explained."""
    return split() and os.environ.get("STUDIO_LAYER_SPLIT", "off").strip().lower() in ("1", "on", "yes", "true")


def split_block() -> int:
    try:
        return int(os.environ.get("STUDIO_SPLIT_BLOCK", "25"))
    except ValueError:
        return 25


def layer_gpu() -> int:
    try:
        return int(os.environ.get("STUDIO_LAYER_GPU", str(encoder_gpu())))
    except ValueError:
        return encoder_gpu()


def h3_bf16(name: str) -> bool:
    """A MiniMax H3-family transformer in bf16 (FastH3, H3's own FL2VA, Ref2VA): what the layer split takes."""
    low = name.lower()
    return ("minimax_h3" in low or "fasth3" in low) and "bf16" in low and "int8" not in low and "fp8" not in low


def place_layers(out: dict) -> dict:
    """Each final use of an H3 bf16 model (a guider, a scheduler, a sampler, an action's own node) reads it through
    StudioSplitH3Blocks, unless TensorFold's engine already holds its blocks or a split is there already."""

    def chain(ref):
        seen = []
        while isinstance(ref, list) and len(ref) == 2 and str(ref[0]) in out:
            node = out[str(ref[0])]
            seen.append(node)
            if node.get("class_type") == "UNETLoader":
                return seen
            ref = node["inputs"].get("model")
        return None

    taken = {int(key) for key in out if str(key).isdigit()}
    next_id = max(taken, default=0) + 2000
    new_for = {}
    for node_id, node in list(out.items()):
        if node.get("class_type") in MODEL_STEPS:
            continue
        ref = node["inputs"].get("model")
        if not (isinstance(ref, list) and len(ref) == 2):
            continue
        found = chain(ref)
        if not found or not h3_bf16(str(found[-1]["inputs"].get("unet_name", ""))):
            continue
        if any(n.get("class_type") in ("TensorFoldH3Blocks", "StudioSplitH3Blocks") for n in found):
            continue
        key = (str(ref[0]), ref[1])
        if key not in new_for:
            new_for[key] = str(next_id)
            out[str(next_id)] = {"class_type": "StudioSplitH3Blocks",
                                 "inputs": {"model": [key[0], key[1]], "unet_name": found[-1]["inputs"]["unet_name"],
                                            "split": split_block(), "device": f"gpu:{layer_gpu()}"}}
            next_id += 1
        node["inputs"]["model"] = [new_for[key], 0]
    return out


def describe() -> str:
    """One line for the logs: how the cards are used."""

    found = cards()
    if not split():
        return "one card" if len(found) <= 1 else f"{len(found)} cards, split off (STUDIO_GPU_SPLIT)"
    return (f"{len(found)} cards: diffusion model on gpu:0, text encoders on gpu:{encoder_gpu()}, "
            f"decoders on gpu:{decoder_gpu()}"
            + (f"; a bf16 H3 transformer's blocks {split_block()}+ on gpu:{layer_gpu()}" if layer_split() else ""))


if __name__ == "__main__":
    import json
    print(json.dumps({"cards": cards(fresh=True), "split": split(), "plan": describe()}, indent=1))
