#!/usr/bin/env python3
"""Render images: Qwen-Image-2.1 through TensorFold's transformer, sampler and image decoder.

The prompt encoder (Qwen3-VL language model and tokenizer) is borrowed from mflux, which must be importable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def encode_prompt(model_dir: str, prompt: str):
    """Prompt embeddings (1, tokens, 4096) from mflux's Qwen-Image-2.1 prompt encoder, taken before the language
    model's final RMSNorm: the transformer is conditioned on the last decoder layer's output before that norm
    (diffusers' QwenImage21Pipeline hooks the norm out, ComfyUI on the Sparks does the same). mflux's text-to-image
    encoder applies it, which moves the picture and, with the Turbo nodes, puts a fine mesh over textured areas."""

    import mlx.core as mx
    from mlx import nn
    from mflux.models.common.tokenizer import TokenizerLoader
    from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_prompt_encoder import Qwen21PromptEncoder
    from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_text_encoder import Qwen21TextEncoder
    from mflux.models.qwen21.qwen21_initializer import Qwen21Initializer
    from mflux.models.qwen21.weights.qwen21_weight_definition import Qwen21WeightDefinition

    class TextOnly(Qwen21WeightDefinition):
        @staticmethod
        def get_components():
            return [c for c in Qwen21WeightDefinition.get_components() if c.name == "text_encoder"]

    class Holder:
        pass

    holder = Holder()
    holder.text_encoder = Qwen21TextEncoder()
    tokenizers = TokenizerLoader.load_all(definitions=Qwen21WeightDefinition.get_tokenizers(), model_path=model_dir)
    Qwen21Initializer.load_components(holder, Path(model_dir), TextOnly, None)
    holder.text_encoder.norm = nn.Identity()  # after loading, so its weight still finds its place
    embeds, mask = Qwen21PromptEncoder.encode_prompt(prompt=prompt, prompt_cache={}, tokenizer=tokenizers["qwen21"],
                                                     text_encoder=holder.text_encoder)
    if int(mx.sum(mask).item()) != mask.shape[1]:
        raise ValueError("a single prompt should carry no padding")
    mx.eval(embeds)
    del holder
    mx.clear_cache()
    return embeds


def saved_denoise(dit, text, width: int, height: int, nodes, seed: int, on_step):
    """Euler over a checkpoint's own saved noise levels (Qwen-Image-2.1-Turbo's sample_sigmas) as saved, then 0: no
    resolution shift, as ComfyUI's ManualSigmas runs them on the Sparks. TensorFold's sampler shifts fixed nodes for
    the image size (right for an adapter's training nodes): on the Turbo nodes that starts the last step from 0.56
    instead of 0.41 at 1024x576 and leaves a fine regular mesh over grass, rock and water."""

    import mlx.core as mx
    import numpy as np
    from tensorfold.families.qwen_image import sampler
    from tensorfold.families.qwen_image.config import latent_grid

    rows, columns = latent_grid(width, height)
    levels = np.concatenate([np.asarray(nodes, dtype=np.float32), np.zeros(1, dtype=np.float32)])
    if levels[0] > 1 or np.any(np.diff(levels) >= 0):
        raise ValueError("nodes must fall from at most 1 towards 0")
    prefix = dit.prefix(text, rows, columns)
    x = sampler.start_noise(seed, width, height, dit.config.in_channels).astype(mx.float32)
    for index in range(len(nodes)):
        velocity = dit(x, float(levels[index]), prefix)
        x = x + velocity.astype(mx.float32) * float(levels[index + 1] - levels[index])
        mx.eval(x)
        on_step(index, len(nodes))
    return x.reshape(1, rows, columns, -1).transpose(0, 3, 1, 2)


def guided_denoise(dit, text, negative, width: int, height: int, steps: int, seed: int, cfg: float, on_step):
    """The base model's Euler loop with true classifier-free guidance, as Qwen's own pipeline runs it: the prompt and
    the negative prompt each step, mixed by ``cfg`` and brought back to the prompt prediction's norm."""

    import mlx.core as mx
    from tensorfold.families.qwen_image import sampler
    from tensorfold.families.qwen_image.config import latent_grid
    from tensorfold.families.qwen_image.schedule import sigmas as make_sigmas

    rows, columns = latent_grid(width, height)
    levels = make_sigmas(steps, width, height, None)
    positive, opposite = dit.prefix(text, rows, columns), dit.prefix(negative, rows, columns)
    x = sampler.start_noise(seed, width, height, dit.config.in_channels).astype(mx.float32)
    total = len(levels) - 1
    for index in range(total):
        level = float(levels[index])
        cond = dit(x, level, positive).astype(mx.float32)
        uncond = dit(x, level, opposite).astype(mx.float32)
        mixed = uncond + cfg * (cond - uncond)
        mixed = mixed * (mx.linalg.norm(cond, axis=-1, keepdims=True) / mx.linalg.norm(mixed, axis=-1, keepdims=True))
        x = x + mixed * float(levels[index + 1] - levels[index])
        mx.eval(x)
        on_step(index, total)
    return x.reshape(1, rows, columns, -1).transpose(0, 3, 1, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--width", type=int, default=1344)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--seeds", help="comma-separated seeds: one image each from one load, written as NAME_s<seed>.png")
    parser.add_argument("--nodes", help="comma-separated noise levels: a distilled adapter's training nodes, shifted for "
                        "the image size; with --raw-nodes a checkpoint's own saved schedule, run as given")
    parser.add_argument("--raw-nodes", action="store_true",
                        help="run --nodes as given, without the resolution shift (Qwen-Image-2.1-Turbo's sample_sigmas)")
    parser.add_argument("--lora", help="adapter safetensors to merge before quantizing")
    parser.add_argument("--no-int8", action="store_true", help="keep every projection in bfloat16")
    parser.add_argument("--vae-dtype", default="float32", choices=("float32", "bfloat16"))
    parser.add_argument("--save-latents")
    parser.add_argument("--cfg", type=float, default=1.0,
                        help="true classifier-free guidance for the base model (Qwen's pipeline: 4); 1 runs without it")
    parser.add_argument("--negative", default=" ", help="the negative prompt for --cfg above 1")
    args = parser.parse_args()
    prompt = Path(args.prompt_file).read_text().strip() if args.prompt_file else args.prompt
    if not prompt:
        parser.error("give --prompt or --prompt-file")

    import mlx.core as mx
    import numpy as np
    from PIL import Image

    from tensorfold.families.qwen_image import lora, sampler, vae, weights

    started = time.perf_counter()
    text = encode_prompt(args.model_dir, prompt)
    guided = args.cfg > 1.0 and not args.nodes
    negative = encode_prompt(args.model_dir, args.negative) if guided else None
    text_s = time.perf_counter() - started

    mark = time.perf_counter()
    dit = weights.load_dit(args.model_dir)
    if args.lora:
        print("[tensorfold] " + json.dumps({"adapter": Path(args.lora).name, **lora.merge(dit, args.lora)}))
    changed = {} if args.no_int8 else weights.int8(dit)
    if args.lora:
        lora.settle(dit)
    load_s = time.perf_counter() - mark

    mark = time.perf_counter()
    nodes = tuple(float(v) for v in args.nodes.split(",")) if args.nodes else None
    forwards = (len(nodes) if nodes else args.steps) * (2 if guided else 1)
    seeds = [int(v) for v in args.seeds.split(",")] if args.seeds else [args.seed]
    target = Path(args.output)
    outputs = [target.with_name(f"{target.stem}_s{seed}{target.suffix}") for seed in seeds] if args.seeds else [target]
    drawn = []
    for seed in seeds:
        step = lambda i, n: print(f"[tensorfold] step {i + 1}/{n}", file=sys.stderr)  # noqa: E731
        if guided:
            latents = guided_denoise(dit, text, negative, args.width, args.height, args.steps, seed, args.cfg, step)
        elif nodes and args.raw_nodes:
            latents = saved_denoise(dit, text, args.width, args.height, nodes, seed, step)
        else:
            latents = sampler.denoise(dit, text, args.width, args.height, args.steps, seed, nodes, on_step=step)
        mx.eval(latents)
        drawn.append(latents)
    denoise_s = time.perf_counter() - mark
    if args.save_latents:
        mx.save_safetensors(args.save_latents, {f"latents_{seed}": z for seed, z in zip(seeds, drawn)})
    del dit
    mx.clear_cache()

    mark = time.perf_counter()
    decoder = vae.load_decoder(args.model_dir, getattr(mx, args.vae_dtype))
    target.parent.mkdir(parents=True, exist_ok=True)
    for latents, path in zip(drawn, outputs):
        image = decoder.decode(latents)
        mx.eval(image)
        Image.fromarray((np.asarray(image[0]) * 255.0 + 0.5).astype(np.uint8)).save(path)
    decode_s = time.perf_counter() - mark
    print("[tensorfold] " + json.dumps({
        "output": [str(path) for path in outputs] if args.seeds else args.output, "width": args.width,
        "height": args.height, "text_tokens": int(text.shape[1]), "cfg": args.cfg if guided else 1.0, "images": len(seeds), "forwards": forwards,
        "int8": changed, "text_s": round(text_s, 1), "load_s": round(load_s, 1), "denoise_s": round(denoise_s, 1),
        "per_forward_s": round(denoise_s / (forwards * len(seeds)), 3), "decode_s": round(decode_s, 1),
        "total_s": round(time.perf_counter() - started, 1), "peak_gib": round(mx.get_peak_memory() / 2**30, 1)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
