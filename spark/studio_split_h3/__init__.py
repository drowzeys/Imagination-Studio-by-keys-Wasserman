"""ComfyUI node: the second part of MiniMax H3's transformer blocks on another card (two-GPU workstations).

A MiniMax H3 / FastH3 / Ref2VA / FL2VA transformer in bf16 is 40 to 44 GB: on a 48 GB card it fits only by ComfyUI
streaming part of its weights from the machine's RAM on every pass. This node keeps blocks [0, split) where ComfyUI
put the model (gpu:0) and runs blocks [split, 50) from a copy of their weights on the second card, read straight from
the checkpoint file (with the model's LoRA patches on those blocks merged in), through ComfyUI's per-block replace
patches (patches_replace["dit"][("double_block", i)]). Only the hidden state crosses between the cards: once on the
way out at block ``split``, once back after the last block, every pass. ComfyUI's own copy of the moved blocks is
never run, so its dynamic loader never brings their weights onto the first card.

The two halves run one after the other (one clip, one sample): the gain is in not streaming weights, not in running
the cards at the same time.

STUDIO_SPLIT_CHECK=1 compares one moved block against ComfyUI's own (cosine) on the first pass, in the log.
"""

import gc
import logging
import os
import time
import uuid

import torch

import comfy.lora
import comfy.model_management
import comfy.model_prefetch
import comfy.ops  # noqa: F401  (disable_weight_init: the layers ComfyUI builds this model from)
import comfy.patcher_extension
import folder_paths
from comfy.ldm.minimax.model import DiTBlock, MiniMaxH3Model

_CACHE: dict = {}  # (path, split, device, patch signature) -> {index: DiTBlock on the second card}


def release():
    """Drop every block copy on the second card (ComfyUI's unload_all_models: the Studio's /free)."""
    if _CACHE:
        _CACHE.clear()
        gc.collect()
        torch.cuda.empty_cache()
        logging.info("Studio split H3: released the blocks on the second card")


_unload = comfy.model_management.unload_all_models


def _unload_all_models(*args, **kwargs):
    release()
    return _unload(*args, **kwargs)


comfy.model_management.unload_all_models = _unload_all_models


def to(value, device):
    """``value`` (a tensor, or tuples and lists of them) on ``device``. Card to card goes through the host: under
    ComfyUI's dynamic VRAM (comfy-aimdo) a direct peer copy between two cards reads back zeros, without an error
    (measured on two RTX PRO 5000: cosine 0.0 against the source; host to card and every kernel are exact)."""
    if torch.is_tensor(value):
        if value.is_cuda and torch.device(device).type == "cuda" and value.device != torch.device(device):
            torch.cuda.synchronize(value.device)
            return value.to("cpu").to(device)
        return value.to(device)
    if isinstance(value, tuple):
        return tuple(to(v, device) for v in value)
    if isinstance(value, list):
        return [to(v, device) for v in value]
    return value


def signature(patches):
    return tuple(sorted((key, tuple((p[0], id(p[1]), p[2], p[3]) for p in value)) for key, value in patches.items()))


class Second:
    """The moved blocks: built on the first pass (after the text encoder is done with the second card), kept until
    ComfyUI unloads its models."""

    def __init__(self, model, path, split, device, patches, unet_config):
        self.model, self.path, self.split, self.device, self.patches = model, path, split, device, patches
        self.unet_config = unet_config
        self.last = len(model.blocks) - 1
        self.key = (path, split, str(device), signature(patches))
        self.home = None
        self.moved = {}  # per pass: the arguments already on the second card
        self.checked = os.environ.get("STUDIO_SPLIT_CHECK") != "1"

    def blocks(self):
        found = _CACHE.get(self.key)
        if found is not None:
            return found
        release()  # another split's copies (another checkpoint, another LoRA) make room first
        from safetensors import safe_open
        mark = time.perf_counter()
        cfg, m = self.unet_config, self.model
        curve = {"apply_silu": not m.use_adaln_curves, "adaln_dtype": torch.float32 if m.use_adaln_curves else m.dtype}
        compute = comfy.model_management.lora_compute_dtype(self.device)
        built = {}
        with comfy.model_prefetch.pause_malloc_graph(sync=True), torch.cuda.device(self.device), \
                safe_open(self.path, framework="pt", device="cpu") as f:
            names = set(f.keys())
            prefix = "" if f"blocks.{self.split}.norm1.weight" in names else "model.diffusion_model."
            for index in range(self.split, len(m.blocks)):
                block = DiTBlock(cfg.get("hidden_size", 5376), cfg.get("num_attention_heads", 56),
                                 cfg.get("attention_head_dim", 128), cfg.get("ffn_hidden_size", 14336),
                                 cfg.get("time_embed_dim", 2688), cfg.get("norm_eps", 1e-5), cfg.get("qk_norm_eps", 1e-5),
                                 **curve, gate_compress=cfg.get("gate_compress", False), dtype=m.dtype, device="meta",
                                 operations=comfy.ops.disable_weight_init)
                state = {}
                head = f"{prefix}blocks.{index}."
                for key in sorted(k for k in names if k.startswith(head)):
                    name = key[len(head):]
                    weight = f.get_tensor(key)
                    if weight.dtype not in (torch.bfloat16, torch.float16, torch.float32):
                        raise ValueError(f"Studio split H3: {key} is {weight.dtype}; this node takes a bf16/fp16 checkpoint")
                    patch = self.patches.get(f"diffusion_model.blocks.{index}.{name}")
                    if patch:
                        merged = comfy.lora.calculate_weight(patch, weight.to(self.device, dtype=compute, copy=True),
                                                             f"diffusion_model.blocks.{index}.{name}")
                        weight = merged.to(weight.dtype)
                    # the dtypes ComfyUI holds this model in (the adaln curve's projection in float32, the rest in the
                    # model's): plain layers then never cast, so comfy-aimdo's cast buffers are never involved
                    want = torch.float32 if name.startswith("adaln_proj.") and m.use_adaln_curves else m.dtype
                    state[name] = weight.to(self.device, dtype=want)
                block.load_state_dict(state, assign=True)
                left = [n for n, p in list(block.named_parameters()) + list(block.named_buffers()) if p.is_meta]
                if left:
                    raise KeyError(f"Studio split H3: block {index} has no weights for {left} in {os.path.basename(self.path)}")
                built[index] = block.eval().requires_grad_(False)
                del state
        _CACHE[self.key] = built
        logging.info(f"Studio split H3: blocks {self.split}-{self.last} on {self.device} "
                     f"({sum(p.numel() * p.element_size() for b in built.values() for p in b.parameters()) / 2**30:.1f} GiB"
                     f"{', ' + str(len(self.patches)) + ' LoRA patches merged' if self.patches else ''}) "
                     f"in {time.perf_counter() - mark:.1f} s")
        return built

    def run(self, index):
        def replace(args, extra):
            # ComfyUI records the first card's allocations block by block (its malloc graph, comfy-aimdo) and replays
            # them; what this block allocates on the second card must stay out of that record
            with comfy.model_prefetch.pause_malloc_graph(sync=index == self.split):
                return step(args, extra)

        def step(args, extra):
            blocks = self.blocks()
            h = args["img"]
            if index == self.split:
                # the hidden state crosses to the second card here: ComfyUI computes on streams of its own (async
                # weight offloading), so the first card is finished before the copy reads, and the copy before the
                # second card computes
                self.home = h.device
                torch.cuda.synchronize(self.home)
                self.moved = {name: to(args[name], self.device) for name in ("t_emb", "mod_segments", "rope_freqs")}
                h = to(h, self.device)
                torch.cuda.synchronize(self.device)
                if not self.checked:
                    self.check(index, args, extra, blocks)
            with torch.cuda.device(self.device):
                out = blocks[index](to(h, self.device), self.moved["t_emb"], self.moved["mod_segments"],
                                    self.moved["rope_freqs"], transformer_options=args["transformer_options"],
                                    attention=None)
                if index == self.last:
                    torch.cuda.synchronize(self.device)
                    out = to(out, self.home)
                    torch.cuda.synchronize(self.home)
                    self.moved = {}
            return {"img": out}
        return replace

    def check(self, index, args, extra, blocks):
        # a block adds its gated residual into its input in place (_mod_gate): each side gets its own copy
        self.checked = True
        want = extra["original_block"]({**args, "img": args["img"].clone()})["img"].float()
        with torch.cuda.device(self.device):
            got = blocks[index](to(args["img"], self.device), self.moved["t_emb"], self.moved["mod_segments"],
                                self.moved["rope_freqs"], transformer_options=args["transformer_options"]).float()
        torch.cuda.synchronize(self.device)
        torch.cuda.synchronize(want.device)
        cos = torch.nn.functional.cosine_similarity(to(got.flatten(), want.device), want.flatten(), dim=0).item()
        logging.info(f"Studio split H3 check: block {index} on {self.device} against ComfyUI's own: cosine {cos:.6f}")


class StudioSplitH3Blocks:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "unet_name": (folder_paths.get_filename_list("diffusion_models"),
                          {"tooltip": "The bf16 checkpoint the model was loaded from (its blocks are read again from it)."}),
            "split": ("INT", {"default": 25, "min": 1, "max": 63, "tooltip": "The first block that runs on the second card."}),
            "device": ("STRING", {"default": "gpu:1", "tooltip": "The second card (gpu:N, ComfyUI's numbering)."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "advanced/multigpu"

    def patch(self, model, unet_name, split, device):
        diffusion_model = model.get_model_object("diffusion_model")
        if not isinstance(diffusion_model, MiniMaxH3Model):
            raise ValueError("Studio split H3 needs a MiniMax H3 / FastH3 model")
        target = comfy.model_management.resolve_gpu_device_option(device)
        same = target == model.load_device and os.environ.get("STUDIO_SPLIT_SAME_DEVICE") == "1"  # a diagnostic
        if target is None or target.type != "cuda" or (target == model.load_device and not same) or split >= len(diffusion_model.blocks):
            logging.info(f"Studio split H3: {device} is not a second card here: the model stays on {model.load_device}")
            return (model,)
        path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
        m = model.clone()
        taken = {}
        for key in list(m.patches):
            if isinstance(key, str) and key.startswith("diffusion_model.blocks."):
                if int(key.split(".")[2]) >= split:
                    taken[key] = m.patches.pop(key)
        if taken:
            m.patches_uuid = uuid.uuid4()  # ComfyUI's loaded copy no longer matches this one's patches
        state = Second(diffusion_model, path, split, target, taken, model.model.model_config.unet_config)
        for i in range(split, len(diffusion_model.blocks)):
            m.set_model_patch_replace(state.run(i), "dit", "double_block", i)

        def no_prefetch(executor, x, timestep, context, transformer_options={}, **kwargs):
            # ComfyUI would stream its own copy of every block ahead of it, the moved ones too
            transformer_options["prefetch_dynamic_vbars"] = False
            # and it records the first card's allocations through the pass to replay them (comfy-aimdo's malloc graph);
            # a pass that leaves for another card cannot be replayed on one, so the record is dropped here, as
            # ComfyUI's own multi-GPU code does (comfy/multigpu.py)
            comfy.model_prefetch.cleanup_malloc_graph()
            return executor(x, timestep, context, transformer_options, **kwargs)

        m.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, "studio_split_h3", no_prefetch)
        logging.info(f"Studio split H3: blocks 0-{split - 1} on {model.load_device}, {split}-{len(diffusion_model.blocks) - 1} on {target}")
        return (m,)


NODE_CLASS_MAPPINGS = {"StudioSplitH3Blocks": StudioSplitH3Blocks}
NODE_DISPLAY_NAME_MAPPINGS = {"StudioSplitH3Blocks": "Studio Split H3 Blocks (two GPUs)"}
