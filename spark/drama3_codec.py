"""Fish Audio Drama 3's codec (the checkpoint's codec.pth) into codec-v1.safetensors, the layout TensorFold's CUDA
engine reads (drama3-generate --codec). Once per machine, offline after the first fetch; scripts/setup-voice.sh runs it
on a DGX Spark with the Studio's own PyTorch environment ($PREFIX/venv):

    $PREFIX/venv/bin/python -I spark/drama3_codec.py CHECKPOINT_DIR OUT.safetensors [--work DIR]

No converted Fish weights are published anywhere: each machine converts its own copy of Fish's codec.pth.

The conversion is TensorFold's own (tools/drama3/codec_convert.py at the drama3-cuda commit the engine is built from).
It builds the codec with Fish's reference code: the Fish S2 modded DAC (modded_dac.py, rvq.py, as packaged in
sgl-project/sglang-omni's fishaudio_s2_pro at a pinned commit) over descript-audio-codec 1.0.0's layers. Those few
files are fetched at pinned commits, each checked against its SHA-256 below, and imported as they are: the two
packages they import for nothing the conversion runs (audiotools: AudioSignal, ml.BaseModel) are stood in for by
torch.nn.Module and an empty class, as the engine's author's vendored copy does. Nothing is installed into the venv
(it needs torch, safetensors, numpy, einops and tqdm, which the Studio's environment has).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import struct
import sys
import types
import urllib.request
from pathlib import Path

DRAMA3_COMMIT = "81173e6c612739a3427844fd8131cf4905e0a9c4"  # TensorFold drama3-cuda, as spark/build-drama3.sh
SGLANG_OMNI = "416a70c0448c1da236e11565195394190ec239cd"
DAC = "1.0.0"
S2 = f"https://raw.githubusercontent.com/sgl-project/sglang-omni/{SGLANG_OMNI}/sglang_omni/models/fishaudio_s2_pro/fish_speech/models/dac"
DESCRIPT = f"https://raw.githubusercontent.com/descriptinc/descript-audio-codec/{DAC}/dac"
# path under the work folder -> (address, SHA-256)
SOURCES = {
    "codec_convert.py": (f"https://raw.githubusercontent.com/drowzeys/TensorFold/{DRAMA3_COMMIT}/tools/drama3/codec_convert.py",
                         "536fea46e0ce74ddfa6baf2c120033566393fb49366b7b14859cdb22cf968d9f"),
    "fishcodec/modded_dac.py": (f"{S2}/modded_dac.py", "e9825cb4a0adb0cd60af047a553097f3dd2985b6a6c4b922890315f407dd00b6"),
    "fishcodec/rvq.py": (f"{S2}/rvq.py", "8bf409f116f51a51927e24956e047f55173e6fcca75567de4b61ae219efa3b6e"),
    "dac/model/base.py": (f"{DESCRIPT}/model/base.py", "2361726838faae352f1265d514e3e2f8d978a2b278dcf66bb5799dfbd2ba7b83"),
    "dac/nn/layers.py": (f"{DESCRIPT}/nn/layers.py", "ec2649649d787b166a138d5ad9dcd585aeabbcd93670771b30cbbbab731c4b63"),
    "dac/nn/quantize.py": (f"{DESCRIPT}/nn/quantize.py", "e2dc61f32f6123aa48a0aeb934a0d9a41ea29a5eae8db28119c791885c9f1b07"),
}
FORMAT = "drama3-codec-v1"
MIN_BYTES = 100 << 20  # the converted codec is about 1.6 GB


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(work: Path, opener=urllib.request.urlopen) -> None:
    """Every pinned source into ``work``, checked against its SHA-256 (one already there and matching is kept)."""

    for name, (url, want) in SOURCES.items():
        path = work / name
        if path.is_file() and sha256(path) == want:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with opener(url) as reply:
            data = reply.read(8 << 20)
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            raise SystemExit(f"{url} is not the file this converter was checked with (sha256 {got[:12]}, wanted {want[:12]})")
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        part.replace(path)


def stand_ins() -> None:
    """The two names the reference code imports from audiotools (Descript's audio toolkit), for nothing run here."""

    import torch

    audiotools = types.ModuleType("audiotools")
    audiotools.AudioSignal = type("AudioSignal", (), {})
    ml = types.ModuleType("audiotools.ml")
    ml.BaseModel = torch.nn.Module  # what the engine's vendored copy uses: BaseModel only adds save/load helpers
    audiotools.ml = ml
    sys.modules.setdefault("audiotools", audiotools)
    sys.modules.setdefault("audiotools.ml", ml)


def package(name: str, path: Path) -> None:
    """An empty package at ``path`` (descript-audio-codec's own __init__ files import its whole model and CLI)."""

    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_codec(path: str, device):
    """Fish's codec from CHECKPOINT/codec.pth, as fish_speech/configs/modded_dac_vq.yaml describes it (the engine's
    author's drama3_ref.build_codec)."""

    from functools import partial

    import torch

    from fishcodec.modded_dac import DAC, ModelArgs, WindowLimitedTransformer
    from fishcodec.rvq import DownsampleResidualVectorQuantize

    def tcfg():
        return ModelArgs(block_size=2048, n_layer=8, n_head=16, dim=1024, intermediate_size=3072,
                         n_local_heads=-1, head_dim=64, rope_base=10000, norm_eps=1e-5,
                         dropout_rate=0.1, attn_dropout_rate=0.1, channels_first=True)

    quantizer = DownsampleResidualVectorQuantize(
        input_dim=1024, n_codebooks=9, codebook_size=1024, codebook_dim=8, quantizer_dropout=0.5,
        downsample_factor=[2, 2], semantic_codebook_size=4096,
        post_module=WindowLimitedTransformer(causal=True, window_size=128, input_dim=1024, config=tcfg()),
        pre_module=WindowLimitedTransformer(causal=True, window_size=128, input_dim=1024, config=tcfg()),
    )
    general = partial(ModelArgs, block_size=8192, n_local_heads=-1, head_dim=64, rope_base=10000,
                      norm_eps=1e-5, dropout_rate=0.1, attn_dropout_rate=0.1, channels_first=True)
    codec = DAC(sample_rate=44100, encoder_dim=64, encoder_rates=[2, 4, 8, 8], decoder_dim=1536,
                decoder_rates=[8, 8, 4, 2], encoder_transformer_layers=[0, 0, 0, 4],
                decoder_transformer_layers=[4, 0, 0, 0], transformer_general_config=general,
                quantizer=quantizer)
    sd = torch.load(os.path.join(path, "codec.pth"), map_location="cpu", mmap=True, weights_only=True)
    res = codec.load_state_dict(sd, strict=False, assign=True)
    unexpected = [k for k in res.unexpected_keys if not k.endswith(("freqs_cis", "causal_mask"))]
    if res.missing_keys or unexpected:
        raise SystemExit(f"codec.pth does not fit Fish's codec: missing={list(res.missing_keys)[:10]} unexpected={unexpected[:10]}")
    return codec.eval().to(device=device, dtype=torch.float32)


def converter(work: Path):
    """TensorFold's codec_convert module, its reference code importable, with build_codec behind ``drama3_ref``."""

    stand_ins()
    package("dac", work / "dac")
    package("dac.model", work / "dac" / "model")
    package("dac.nn", work / "dac" / "nn")
    package("fishcodec", work / "fishcodec")
    for name in ("dac.nn.layers", "dac.nn.quantize", "dac.model.base", "fishcodec.rvq", "fishcodec.modded_dac"):
        load(name, work / (name.replace(".", "/") + ".py"))
    ref = types.ModuleType("drama3_ref")
    ref.build_codec = build_codec
    sys.modules["drama3_ref"] = ref
    return load("drama3_codec_convert", work / "codec_convert.py")


def header(path: Path) -> dict:
    """A safetensors file's JSON header ({} when it is not one)."""

    try:
        with open(path, "rb") as f:
            size = struct.unpack("<Q", f.read(8))[0]
            if not 0 < size < (64 << 20):
                return {}
            return json.loads(f.read(size))
    except (OSError, ValueError, struct.error):
        return {}


def converted(path: Path) -> bool:
    """Whether ``path`` is a finished conversion (size and the format the engine checks)."""

    try:
        return path.stat().st_size >= MIN_BYTES and (header(path).get("__metadata__") or {}).get("format") == FORMAT
    except OSError:
        return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="drama3_codec.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("checkpoint", help="Fish's Drama 3 checkpoint folder (holds codec.pth)")
    parser.add_argument("out", help="where codec-v1.safetensors goes")
    parser.add_argument("--work", default=None, help="where the pinned reference code is kept (default: OUT's folder/.drama3-codec-src)")
    args = parser.parse_args(argv)
    checkpoint, out = Path(args.checkpoint).expanduser(), Path(args.out).expanduser()
    if not (checkpoint / "codec.pth").is_file():
        print(f"no codec.pth in {checkpoint}", file=sys.stderr)
        return 1
    if converted(out):
        print(f"already converted: {out}")
        return 0
    work = Path(args.work).expanduser() if args.work else out.parent / ".drama3-codec-src"
    fetch(work)
    module = converter(work)
    import torch

    torch.set_num_threads(max(1, min(16, os.cpu_count() or 1)))
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    module.convert(str(checkpoint), str(part))
    if not converted(part):
        part.unlink(missing_ok=True)
        print("the conversion wrote no codec-v1 file", file=sys.stderr)
        return 1
    os.chmod(part, 0o644)
    part.replace(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
