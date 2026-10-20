"""The Mac's own MiniMax H3 files for ComfyUI: the Library actions read the weights the MLX engine already has.

A DGX Spark has ComfyUI's single-file copies of MiniMax H3 (Comfy-Org/MiniMax-H3, FastVideo-FastH3-Comfy). A Mac has
MiniMax's own Hugging Face layout in H3_MODEL_DIR (oneshot-setup.sh --ref2va): the transformer in 13 shards, the text
encoder in 14, the video decoder as one fp32 file. Copying them again in ComfyUI's layout would take 70 GB of disk
for the same numbers, so mac/comfy.sh writes small pointer files instead (`*.studio.sft`, JSON) into the folders
ComfyUI lists, and this node reads a pointer as the state dict ComfyUI's own loaders want:

    h3-dit        the transformer's shards with MiniMax's own names, two things changed into the form of Comfy-Org's
                  pruned file, which is what a DGX Spark runs: each attention's fused projection regrouped from
                  MiniMax's order, head by head (q, k, v of head 0, then of head 1, ...), to ComfyUI's (all the q
                  rows, then k, then v), and the adaLN projections put on a basis of 8 curves of the time embedding
                  (`curves`), which takes the transformer from 63 GB to 37 GB in memory. Every other weight is the
                  same as in Comfy-Org's file.
    h3-text       Qwen3-VL's shards with Hugging Face's prefixes taken off (model.language_model. -> model.,
                  model.visual. -> visual.), cut after language layer 50 as Comfy-Org's file is (the conditioning is
                  that layer's output: without the cut ComfyUI reads layer 64's), without the unused lm_head and final
                  norm, in bfloat16 (a Spark has the 8-bit copy)
    h3-video-vae  the fp32 decoder and encoder (source/model.safetensors) with the latent mean and spread of its config

Nothing is converted on disk: a file that is not a pointer goes to ComfyUI's own loader. Two things change for the
files read through a pointer, both for memory (a Mac's GPU memory is its system memory):

- the text encoder keeps the file's own bfloat16 tensors (assigned, as ComfyUI does on a Spark's dynamic loading)
  instead of a float16 copy of 49 GB: they are pages of the file the system can drop and read again;
- a LoRA's backup of a transformer weight (what ComfyUI puts back when the LoRA is taken off) is that weight as the
  file holds it, not a second copy in memory: measured, the copies took 63 GB beside the 63 GB model and the Upscale
  was killed by the system (198 GB for ComfyUI) before its decode;
- the transformer taken off the GPU (ComfyUI does so to make room for the video decoder) goes back to the file's
  pages instead of being copied into 63 GB of new memory: measured, that copy was the second time the Upscale was
  killed, at the start of its decode.

A third change is for every model on this machine: a 3D convolution on PyTorch's Metal device is done as one 2D
convolution per temporal tap, summed (`conv3d`). PyTorch's own Metal conv3d took 177 GB to encode nine frames of
864x480 with MiniMax H3's video decoder and was killed by the system; the sum of 2D convolutions gives the same
numbers (checked against the CPU) in the memory of the input and the output.

mac/comfy.sh copies this folder into ComfyUI/custom_nodes on every start.
"""
import collections
import json
import logging
import os

import safetensors
import torch
import torch.nn.functional as F

import comfy.model_patcher
import comfy.sd
import comfy.utils

MARK = b'{"studio_files"'
_load = comfy.utils.load_torch_file


def is_pointer(path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(MARK)) == MARK
    except OSError:
        return False


def shards(folder: str) -> list[str]:
    index = os.path.join(folder, "model.safetensors.index.json")
    if os.path.isfile(index):
        with open(index) as handle:
            names = sorted(set(json.load(handle)["weight_map"].values()))
        return [os.path.join(folder, name) for name in names]
    return [os.path.join(folder, "model.safetensors")]


def tensors(files, rename=lambda key: key, convert=lambda key, tensor: tensor):
    out = {}
    for path in files:
        with safetensors.safe_open(path, framework="pt", device="cpu") as handle:
            for key in handle.keys():
                name = rename(key)
                if name is not None:
                    out[name] = convert(name, handle.get_tensor(key))
    return out


def dit_weight(key: str, tensor):
    """MiniMax's fused q/k/v rows, head by head, in ComfyUI's order: all q, all k, all v."""

    if not key.endswith("attn.qkv_proj.weight"):
        return tensor
    heads = tensor.shape[0] // (3 * HEAD_DIM)
    return tensor.reshape(heads, 3, HEAD_DIM, -1).transpose(0, 1).reshape(tensor.shape).contiguous()


GRID, BASIS = 1025, 8   # the curve basis of Comfy-Org's pruned file: 1025 points of t in [0, 1], 8 curves


def curves(sd: dict) -> dict:
    """The adaLN projections on a basis of the time-embedding curve, as ComfyUI's minimax model reads Comfy-Org's
    pruned checkpoint (`adaln_t_table`): every adaLN input is silu(time_embedder(t)) for one t in [0, 1], a curve in
    2688 dimensions that 8 directions span. Its mean over the grid is folded into each bias, the 8 principal directions
    of what is left into each weight, and the curve's coordinates on them make the table ComfyUI interpolates. Measured
    against the full projection at 200 points of t, block 0 and the final layer: within 1.4e-4 (Comfy-Org's own file:
    8.6e-4)."""

    import math

    proj_in_w, proj_in_b = sd.pop("time_embedder.proj_in.weight").float(), sd.pop("time_embedder.proj_in.bias").float()
    proj_out_w, proj_out_b = sd.pop("time_embedder.proj_out.weight").float(), sd.pop("time_embedder.proj_out.bias").float()
    half = proj_in_w.shape[1] // 2
    t = torch.linspace(0.0, 1.0, GRID, dtype=torch.float64).float()
    angles = t[:, None] * torch.exp(-math.log(10000.0) * torch.arange(half, dtype=torch.float32) / half)[None]
    embedded = torch.cat([angles.cos(), angles.sin()], -1)       # ComfyUI's TimeEmbedder: cos before sin, fp32
    curve = F.silu(F.linear(F.silu(F.linear(embedded, proj_in_w, proj_in_b)), proj_out_w, proj_out_b)).double()
    mean = curve.mean(0)
    _, _, vh = torch.linalg.svd(curve - mean, full_matrices=False)
    basis = vh[:BASIS].T                                          # [2688, 8]
    sd["adaln_t_table"] = ((curve - mean) @ basis).float()
    for key in [k for k in sd if k.endswith("adaln_proj.linear.weight")]:
        weight, bias_key = sd[key].double(), key[:-len("weight")] + "bias"
        sd[bias_key] = (sd[bias_key].double() + weight @ mean).float()
        sd[key] = (weight @ basis).float()
    return sd


TEXT_LAYERS = 50   # MiniMax H3 conditions on the hidden state after Qwen3-VL's 50th layer (comfy/text_encoders/minimax.py)
HEAD_DIM = 128


def text_name(key: str):
    if key.startswith("lm_head.") or key == "model.language_model.norm.weight":
        return None
    if key.startswith("model.language_model."):
        rest = key[len("model.language_model."):]
        if rest.startswith("layers.") and int(rest.split(".")[1]) >= TEXT_LAYERS:
            return None
        return "model." + rest
    if key.startswith("model.visual."):
        return key[len("model."):]
    return key


def assemble(spec: dict) -> dict:
    kind, folder = spec["kind"], os.path.expanduser(spec["folder"])
    if kind == "h3-dit":
        return curves(tensors(shards(folder), convert=dit_weight))
    if kind == "h3-text":
        return tensors(shards(folder), text_name)
    if kind == "h3-video-vae":
        with open(os.path.join(folder, "config.json")) as handle:
            config = json.load(handle)
        out = tensors([os.path.join(folder, config.get("source_path", "source"), config.get("source_safetensors_path", "model.safetensors"))])
        out["latents_mean"] = torch.tensor(config["latents_mean"], dtype=torch.float32)
        out["latents_std"] = torch.tensor(config["latents_std"], dtype=torch.float32)
        return out
    raise ValueError(f"unknown kind of Studio file: {kind}")


def load_torch_file(ckpt, safe_load=False, device=None, return_metadata=False):
    if not str(ckpt).lower().endswith(".sft") or not is_pointer(ckpt):
        return _load(ckpt, safe_load=safe_load, device=device, return_metadata=return_metadata)
    with open(ckpt) as handle:
        spec = json.load(handle)["studio_files"]
    logging.info("Studio files: %s from %s", spec["kind"], spec["folder"])
    sd = assemble(spec)
    LAST[spec["kind"]] = spec
    return (sd, None) if return_metadata else sd


LAST: dict = {}   # the pointer read last, by kind: the loader that called load_torch_file takes it from here


class Source:
    """A transformer's own file, tensor by tensor (memory-mapped: the pages are the file's)."""

    def __init__(self, folder: str):
        self.folder, self.handles, self.where = folder, {}, {}
        for path in shards(folder):
            with safetensors.safe_open(path, framework="pt", device="cpu") as handle:
                for key in handle.keys():
                    self.where[key] = path

    def tensor(self, key: str):
        path = self.where.get(key)
        if path is None:
            return None
        if path not in self.handles:
            self.handles[path] = safetensors.safe_open(path, framework="pt", device="cpu")
        return dit_weight(key, self.handles[path].get_tensor(key))


_load_diffusion_model = comfy.sd.load_diffusion_model


def load_diffusion_model(unet_path, *args, **kwargs):
    LAST.pop("h3-dit", None)
    model = _load_diffusion_model(unet_path, *args, **kwargs)
    spec = LAST.pop("h3-dit", None)
    if spec is not None:
        model.model.studio_source = Source(os.path.expanduser(spec["folder"]))
    return model


Backup = collections.namedtuple("Dimension", ["weight", "inplace_update"])
_patch_weight_to_device = comfy.model_patcher.ModelPatcher.patch_weight_to_device


def patch_weight_to_device(self, key, device_to=None, inplace_update=False, return_weight=False, force_cast=False):
    source = getattr(self.model, "studio_source", None)
    if (source is not None and key in self.patches and key not in self.backup and not return_weight
            and not (self.weight_inplace_update or inplace_update) and key.startswith("diffusion_model.")):
        weight = comfy.model_patcher.get_key_weight(self.model, key)[0]
        kept = source.tensor(key[len("diffusion_model."):])
        if kept is not None and kept.shape == weight.shape and kept.dtype == weight.dtype:
            self.backup[key] = Backup(kept, False)
    return _patch_weight_to_device(self, key, device_to=device_to, inplace_update=inplace_update,
                                   return_weight=return_weight, force_cast=force_cast)


_unpatch_model = comfy.model_patcher.ModelPatcher.unpatch_model


def unpatch_model(self, device_to=None, unpatch_weights=True):
    source = getattr(self.model, "studio_source", None)
    if source is None or not unpatch_weights or device_to is None or torch.device(device_to).type != "cpu":
        return _unpatch_model(self, device_to=device_to, unpatch_weights=unpatch_weights)
    result = _unpatch_model(self, device_to=None, unpatch_weights=True)     # the LoRA taken off, nothing moved
    for name, param in list(self.model.named_parameters()):
        if param.device.type == "cpu" or not name.startswith("diffusion_model."):
            continue
        kept = source.tensor(name[len("diffusion_model."):])
        if kept is not None and kept.shape == param.shape and kept.dtype == param.dtype:
            comfy.utils.set_attr_param(self.model, name, kept)
    self.model.to(device_to)
    self.model.device = device_to
    return result


_clip_load_sd = comfy.sd.CLIP.load_sd


def clip_load_sd(self, sd, full_model=False):
    if full_model or LAST.pop("h3-text", None) is None:
        return _clip_load_sd(self, sd, full_model)
    self.patcher.is_dynamic = lambda: True       # ComfyUI then assigns the tensors instead of copying them
    try:
        return _clip_load_sd(self, sd, full_model)
    finally:
        del self.patcher.is_dynamic


_conv3d = F.conv3d


def triple(value):
    return tuple(value) if isinstance(value, (tuple, list)) else (value,) * 3


def conv3d(x, weight, bias=None, stride=1, padding=0, dilation=1, groups=1):
    if x.device.type != "mps" or isinstance(padding, str) or x.dim() != 5:
        return _conv3d(x, weight, bias, stride, padding, dilation, groups)
    return conv3d_by_taps(x, weight, bias, stride, padding, dilation, groups)


def conv3d_by_taps(x, weight, bias=None, stride=1, padding=0, dilation=1, groups=1):
    """A 3D convolution as one 2D convolution per temporal tap of the kernel, summed."""

    (st, sh, sw), (pt, ph, pw), (dt, dh, dw) = triple(stride), triple(padding), triple(dilation)
    if pt or ph or pw:
        x = F.pad(x, (pw, pw, ph, ph, pt, pt))
    n, c, t, h, w = x.shape
    taps = weight.shape[2]
    t_out = (t - dt * (taps - 1) - 1) // st + 1
    out = None
    for k in range(taps):
        part = x[:, :, k * dt: k * dt + st * (t_out - 1) + 1: st].transpose(1, 2).reshape(n * t_out, c, h, w)
        y = F.conv2d(part, weight[:, :, k], None, (sh, sw), 0, (dh, dw), groups)
        out = y if out is None else out.add_(y)
    out = out.view(n, t_out, *out.shape[1:]).transpose(1, 2)
    if bias is not None:
        out = out + bias.view(1, -1, 1, 1, 1).to(out.dtype)
    return out.contiguous()


F.conv3d = conv3d
comfy.utils.load_torch_file = load_torch_file
comfy.sd.load_diffusion_model = load_diffusion_model
comfy.model_patcher.ModelPatcher.patch_weight_to_device = patch_weight_to_device
comfy.model_patcher.ModelPatcher.unpatch_model = unpatch_model
comfy.sd.CLIP.load_sd = clip_load_sd
NODE_CLASS_MAPPINGS = {}
