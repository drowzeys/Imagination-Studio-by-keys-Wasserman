"""What a LoRA file is made for, read from its safetensors header, and which of the Studio's jobs can merge it.

A LoRA is only useful on the model it was trained on: its tensors are named after that model's own layers. This file
tells the models apart by those names, in whatever layout the trainer saved them (ai-toolkit / ComfyUI `diffusion_model.`,
diffusers `transformer.`, PEFT `base_model.model.`, kohya `lora_unet_`), so the hub can

    * refuse a file that is not a low-rank LoRA at all,
    * file each LoRA under the model it belongs to, and
    * offer a job only the LoRAs it can merge.

`KINDS` lists what the Studio knows. `jobs` says which Studio jobs run that model: a kind with no job is stored in the hub
and shown as such ("stored, no engine yet"); the Studio does not pretend to render with it. `mac` says which of those jobs
the Mac (MLX) build can also do.

    python3 app/lora_kinds.py FILE.safetensors ...     what each file is
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import lora_layout

# id -> label, the job family `target` (loras.resolve), the jobs that merge it on a Spark and on a Mac, and why a job does not
KINDS: dict[str, dict] = {
    "h3": {"label": "MiniMax H3", "short": "Clips", "target": "video", "jobs": ("video",), "mac": ("video",)},
    "qwen-image": {"label": "Qwen-Image 2.x", "short": "Pictures", "target": "image", "jobs": ("scout", "edit"), "mac": ("edit",),
                   "mac_why": {"scout": "a new picture on a Mac merges the LoRA through TensorFold's diffusers layout, which has no fused "
                                        "img_mlp.gate_up layer: use Edit a picture, or a Spark"}},
    "flux": {"label": "Flux", "short": "Stored", "target": None, "jobs": (), "mac": ()},
    "ltx": {"label": "LTX-Video", "short": "Stored", "target": None, "jobs": (), "mac": ()},
    "sd": {"label": "Stable Diffusion", "short": "Stored", "target": None, "jobs": (), "mac": ()},
    "other": {"label": "Unrecognised model", "short": "Stored", "target": None, "jobs": (), "mac": ()},
}
JOBS = {"video": "a clip", "scout": "a new picture", "edit": "Edit a picture"}
LORA_TENSOR = re.compile(r"lora_(?:A|B|down|up)|\.lora\.(?:down|up)\.")
PREFIXES = ("diffusion_model.", "transformer.", "base_model.model.", "unet.", "lora_unet_", "lora_transformer_")


def label(kind: str) -> str:
    return KINDS.get(kind, KINDS["other"])["label"]


def target_of(kind: str) -> str | None:
    """The job family that merges this kind, `video` (MiniMax H3) or `image` (Qwen-Image), or None when no job does."""

    return KINDS.get(kind, KINDS["other"])["target"]


def kind_of_target(target: str | None) -> str:
    """The kind a preset's old `target` field names: a clip LoRA unless it says image."""

    return "qwen-image" if target == "image" else "h3"


def jobs_for(kind: str, spark: bool) -> tuple[str, ...]:
    """The Studio jobs that merge this kind of LoRA on this machine."""

    info = KINDS.get(kind, KINDS["other"])
    return tuple(info["jobs"] if spark else info["mac"])


def why_not(kind: str, job: str, spark: bool) -> str:
    """One sentence saying why `job` cannot merge a LoRA of this kind here ("" when it can)."""

    info = KINDS.get(kind, KINDS["other"])
    if job in jobs_for(kind, spark):
        return ""
    if not info["jobs"]:
        return f"{info['label']} LoRAs are stored in the hub, and the Studio has no {info['label']} engine yet."
    if job in info["jobs"] and not spark:
        return (info.get("mac_why") or {}).get(job) or f"{JOBS.get(job, job)} does not merge {info['label']} LoRAs on a Mac."
    return f"It is a {info['label']} LoRA: it cannot go on {JOBS.get(job, job)}."


def stems(names) -> list[str]:
    """The adapted module of each lora_A / lora_down tensor, flattened to lower-case words joined with `_`, whatever the layout:
    `diffusion_model.transformer_blocks.3.attn.to_q.lora_A.weight` and kohya's `lora_unet_transformer_blocks_3_attn_to_q.lora_down.weight`
    both give `transformer_blocks_3_attn_to_q`."""

    out = []
    for name in names:
        hit = re.match(r"^(?P<m>.+?)\.(?:lora_A(?:\.[A-Za-z0-9_-]+)?|lora_down|lora\.down)\.weight$", name)
        if not hit:
            continue
        module = hit["m"]
        for prefix in PREFIXES:
            if module.startswith(prefix):
                module = module[len(prefix):]
        out.append(re.sub(r"[^a-z0-9]+", "_", module.lower()))
    return out


def top_block(words: list[str]) -> int:
    """The highest index among the adapted `transformer_blocks_N_` modules (-1 when there are none)."""

    return max((int(m[1]) for w in words if (m := re.match(r"^transformer_blocks_(\d+)_", w))), default=-1)


def detect(path: Path) -> dict:
    """{"kind", "family", "layout", "modules", "rank"} of a LoRA file; ValueError when it is not a low-rank LoRA.

    The kind is the model the tensor names belong to; `other` when they are a LoRA's but no model the Studio knows."""

    tensors, _, _ = lora_layout.header(path)
    names = list(tensors)
    if not any(LORA_TENSOR.search(n) for n in names):
        raise ValueError("not a LoRA: it holds no low-rank lora_A / lora_B (or lora_down / lora_up) tensors")
    flat = stems(names)
    if not flat:
        raise ValueError("not a LoRA this hub can read: its tensors are not lora_A / lora_down pairs")

    def has(pattern: str) -> bool:
        return any(re.search(pattern, word) for word in flat)

    layout = lora_layout.layout(names)
    rank = next((min(v["shape"]) for k, v in tensors.items() if re.search(r"lora_(A|down)|\.lora\.down", k) and v.get("shape")), None)
    kind, family = "other", ""
    if has(r"(^|_)(down_blocks|up_blocks|mid_block|input_blocks|output_blocks|middle_block)_\d*"):
        kind = "sd"  # a U-Net: Stable Diffusion 1.x / 2.x / XL
        width = next((v["shape"][-1] for k, v in tensors.items() if re.search(r"attn2[._]to[._]k", k) and re.search(r"lora_(A|down)", k) and v.get("shape")), 0)
        family = {2048: "SDXL", 768: "SD 1.x", 1024: "SD 2.x"}.get(width, "")
    elif layout != "unknown" and any(re.search(r"(^|\.)blocks\.\d+\.(attn|mlp)\.", m) for m in lora_layout.modules(names)):
        kind = "h3"
    elif has(r"(^|_)transformer_blocks_\d+_(img_mlp|txt_mlp|img_mod|txt_mod)") or (
            # an attention-only LoRA has no layer that names Qwen-Image: its blocks do. Qwen-Image has 32 or more double-stream
            # blocks; Flux.1 has 19 and Flux.2 8, and those two are told by their single-stream blocks and their ff_context
            has(r"^transformer_blocks_\d+_attn_(to_q|to_k|to_v|to_out|add_q_proj|to_add_out)")
            and not has(r"single_transformer_blocks|double_blocks|ff_context|_ff_") and top_block(flat) >= 19):
        kind = "qwen-image"
    elif has(r"(^|_)(single_transformer_blocks|double_blocks|single_blocks)_\d+") or has(r"transformer_blocks_\d+_(ff_context|attn_add_q_proj)"):
        kind = "flux"
        family = "Flux.2" if has(r"to_qkv_mlp_proj|ff_linear_in") else "Flux.1"
    elif has(r"(^|_)transformer_blocks_\d+_(attn1|attn2|audio_attn1|audio_attn2|audio_to_video_attn|video_to_audio_attn)"):
        kind = "ltx"
    modules = len(lora_layout.modules(names)) if kind == "h3" else len(flat)
    return {"kind": kind, "family": family, "layout": layout, "modules": modules, "rank": rank}


def main(argv: list[str]) -> int:
    for name in argv:
        try:
            found = detect(Path(name).expanduser())
            print(f"{found['kind']:11s} {label(found['kind'])}{' / ' + found['family'] if found['family'] else ''}  rank {found['rank']}, "
                  f"{found['modules']} modules, {found['layout']} layout  {name}")
        except (OSError, ValueError) as error:
            print(f"refused     {error}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
