#!/usr/bin/env python3
"""YuE2 (m-a-p/YuE2-3B and m-a-p/YuE2-Vae) as a persistent server for the studio's Music tab (app/music_yue2.py).

Run in YuE2's own environment ($PREFIX/yue2/venv, spark/setup-yue2.sh), never the studio's. It speaks the protocol of
the studio's other song and voice engines (app/voice.py, Served): one JSON request a line on stdin, one JSON line a
reply on stderr (a ready line once loaded, progress lines while a song is made, then its answer); every other stderr
line starts with something other than ``{``. It ends at the end of its input, so it dies with the app.

    {"id", "style", "lyrics", "seconds", "seed", "wav", "artifacts"?, "cot"?, "instrumental"?}
    -> {"id", "progress": {"stage": "plan|semantic|nar|vae", "done", "total"}} ...
    -> {"id", "ok": true, "audio_s", "ended", "truncated", "plan_ms", "semantic_ms", "nar_ms", "vae_ms", "total_ms",
        "rtf", "tokens", "peak_memory_gb", "score"?, "instrumental"}

A song: YuE2 writes its score (``cot`` "full": melody and chords in ABC), then the song's semantic tokens (25 a second
of song, so ``seconds`` caps their number: a song the model would have made longer is cut there and faded out), then
the acoustic latents by flow matching, then the decoder makes 48 kHz stereo. The WAV is 24-bit. An instrumental (no
words to sing) follows the yue2-music skill's instrumental workflow (1.2.0): YuE2 plans a score from section tags only,
every Vocal note moves to the Ins voice (its ``convert_score``, MIT, run from the installed source), and that score is
rendered with the section tags as the lyrics.

The model stays loaded between songs, on the GPU with the decoder beside it. The decode path is the PyTorch one by
default (``--backend torch``): bf16 weights, static KV cache, CUDA graphs and FlashAttention for each token (YuE2's own
GraphAR), PyTorch SDPA for the flow matching. ``--quant int8`` adds int8 weight-only copies of the token loop's
projections and output head (per output channel, a Triton matrix-vector kernel), used only for one or two rows at a
time: the score's and song's tokens. The prompt, the flow matching and the decoder keep the checkpoint's own bf16 and
fp32 weights. ``--backend blocks`` runs the token loop on TensorFold's CUDA decode engine (``spark/tensorfold_yue2``
beside this file, ``libtf_yue2.so`` from spark/build-yue2-engine.sh; the Music tab's choice on a DGX Spark where it is
built): its ``prepare(model)`` copies the AR weights into the engine and takes over each token's pass; where the engine
cannot be used the int8 loop above runs instead.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

RATE, FRAMES_S = 48000, 25  # the decoder's sample rate; semantic tokens (and latent frames) a second of song
PLAN_LYRICS = {90: "[Intro]\n\n[Verse]\n\n[Outro]\n", 150: "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Outro]\n",
               10_000: "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Verse]\n\n[Chorus]\n\n[Bridge]\n\n[Chorus]\n\n[Outro]\n"}
FADE_S = 2.0  # a song cut at its length fades out over its last seconds


def say(answer: dict) -> None:
    sys.stderr.write(json.dumps(answer) + "\n")
    sys.stderr.flush()


def note(line: str) -> None:
    sys.stderr.write("[tensorfold] yue2 " + line + "\n")
    sys.stderr.flush()


# ---------- the request, as YuE2 reads it (no torch: tests/test_music_yue2.py imports this part) ----------

def instrumental_style(style: str) -> str:
    """A style for an instrumental, as the skill's instrumental workflow writes it: "Instrumental, " first, and every
    one of its four conditions said."""

    style = " ".join(str(style or "").split()).strip().rstrip(".,") or "Expressive instrumental music"
    if not style.lower().startswith("instrumental"):
        style = "Instrumental, " + style
    for condition in ("no vocals", "no singing", "no choir", "no spoken words"):
        if condition not in style.lower():
            style += ", " + condition
    return style + "."


def plan_lyrics(lyrics: str, seconds: float) -> str:
    """The section tags an instrumental is planned from: the ones the lyrics hold, else a shape for its length."""

    import re

    tags = re.findall(r"^\s*\[([^\]\n]{1,40})\]\s*$", lyrics or "", re.M)
    tags = [t for t in tags if t.strip().lower() != "instrumental"]
    if tags:
        return "\n\n".join("[" + t.strip() + "]" for t in tags) + "\n"
    return next(text for limit, text in sorted(PLAN_LYRICS.items()) if seconds <= limit)


def lyric_tags(abc: str) -> str:
    """The lyrics of a converted instrumental score: its section comments as tags, nothing sung."""

    labels = [line[2:] for line in abc.splitlines() if line.startswith("% ")]
    return "\n\n".join("[" + label.title() + "]" for label in labels) + ("\n" if labels else "")


def budget(seconds: float) -> int:
    """The semantic tokens a song of up to ``seconds`` may take (one a frame, and the end token)."""

    return max(201, min(9000, int(math.ceil(float(seconds) * FRAMES_S)) + 1))


def fade_out(audio, seconds: float = FADE_S):
    """[samples, channels] float audio with its last ``seconds`` faded out (a cosine), in place."""

    import numpy as np

    n = min(len(audio), int(seconds * RATE))
    if n > 1:
        audio[-n:] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, n))))[:, None].astype(audio.dtype)
    return audio


# ---------- int8 weight-only projections for the token loop ----------

def int8_kernel():
    """The Triton matrix-vector kernel: y[m, n] = s[n] * sum_k x[m, k] w[n, k] for one or two rows of x."""

    import triton
    import triton.language as tl

    @triton.autotune(configs=[triton.Config({"BLOCK_N": n, "BLOCK_K": k}, num_warps=w)
                              for n, k, w in ((8, 512, 4), (16, 256, 4), (16, 512, 4), (32, 256, 4), (32, 512, 8),
                                              (64, 256, 8), (16, 1024, 8))],
                     key=["N", "K", "M"])
    @triton.jit
    def gemv(x_ptr, w_ptr, s_ptr, y_ptr, N, K, M: tl.constexpr, BLOCK_N: tl.constexpr, BLOCK_K: tl.constexpr):
        n = tl.program_id(0) * BLOCK_N + tl.arange(0, BLOCK_N)
        keep_n = n < N
        acc0 = tl.zeros((BLOCK_N,), dtype=tl.float32)
        acc1 = tl.zeros((BLOCK_N,), dtype=tl.float32)
        for start in range(0, K, BLOCK_K):
            k = start + tl.arange(0, BLOCK_K)
            keep_k = k < K
            w = tl.load(w_ptr + n[:, None] * K + k[None, :], mask=keep_n[:, None] & keep_k[None, :], other=0).to(tl.float32)
            x0 = tl.load(x_ptr + k, mask=keep_k, other=0).to(tl.float32)
            acc0 += tl.sum(w * x0[None, :], axis=1)
            if M > 1:
                x1 = tl.load(x_ptr + K + k, mask=keep_k, other=0).to(tl.float32)
                acc1 += tl.sum(w * x1[None, :], axis=1)
        s = tl.load(s_ptr + n, mask=keep_n, other=0)
        tl.store(y_ptr + n, (acc0 * s).to(y_ptr.dtype.element_ty), mask=keep_n)
        if M > 1:
            tl.store(y_ptr + N + n, (acc1 * s).to(y_ptr.dtype.element_ty), mask=keep_n)

    return gemv


def int8_linears(model, rows: int = 2) -> dict:
    """Each token-loop projection (AR attention q/k/v/o, AR MLP, the output head) given an int8 weight-only copy,
    per output channel, used for inputs of at most ``rows`` rows; wider inputs (a prompt, the flow matching's prefill)
    keep the bf16 weight. The module stays an nn.Linear with its bf16 weight, so YuE2's GraphAR captures it as it is."""

    import torch
    import triton
    from torch import nn

    gemv = int8_kernel()

    class Int8Linear(nn.Linear):
        def forward(self, x):  # noqa: D401
            flat = x.reshape(-1, self.in_features)
            if flat.shape[0] > rows or x.dtype != torch.bfloat16 or not x.is_cuda:
                return super().forward(x)
            flat = flat.contiguous()
            out = torch.empty((flat.shape[0], self.out_features), device=x.device, dtype=x.dtype)
            gemv[lambda meta: (triton.cdiv(self.out_features, meta["BLOCK_N"]),)](
                flat, self.q8, self.q8_scale, out, self.out_features, self.in_features, M=flat.shape[0])
            if self.bias is not None:
                out += self.bias
            return out.reshape(*x.shape[:-1], self.out_features)

    names = [f"model.layers.{i}.{part}" for i in range(len(model.model.layers))
             for part in ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
                          "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj")] + ["lm_head"]
    held = 0
    with torch.no_grad():
        for name in names:
            linear = model.get_submodule(name)
            weight = linear.weight.detach()
            scale = weight.float().abs().amax(dim=1).clamp_min(1e-8) / 127.0
            q8 = torch.round(weight.float() / scale[:, None]).clamp(-127, 127).to(torch.int8).contiguous()
            linear.__class__ = Int8Linear
            linear.register_buffer("q8", q8, persistent=False)
            linear.register_buffer("q8_scale", scale.contiguous(), persistent=False)
            held += q8.numel() + scale.numel() * 4
        # every shape tuned once here, on the default stream, never inside a graph capture
        for name in names:
            linear = model.get_submodule(name)
            for m in range(1, rows + 1):
                linear(torch.zeros((m, 1, linear.in_features), device=linear.weight.device, dtype=torch.bfloat16))
        torch.cuda.synchronize()
    return {"linears": len(names), "int8_gb": round(held / 1e9, 2)}


# ---------- the PyTorch engine ----------

DTYPES = {"BF16": "bfloat16", "F16": "float16", "F32": "float32", "I64": "int64", "I32": "int32", "U8": "uint8", "I8": "int8"}


def read_weights(path: Path, device: str) -> dict:
    """A safetensors file's tensors on ``device``, read in one sequential pass into memory and copied from there.
    Measured on a GB10 for YuE2-3B (7.3 GB): 1.7 s to read and 0.5 s to the GPU, where copying from the mapped file
    (transformers' and safetensors' own loading) took 42 to 60 s."""

    import torch

    size = path.stat().st_size
    data = bytearray(size)
    with open(path, "rb", buffering=0) as handle:
        view, done = memoryview(data), 0
        while done < size:
            got = handle.readinto(view[done:done + (256 << 20)])
            if not got:
                raise OSError(f"{path} ended early")
            done += got
    length = int.from_bytes(data[:8], "little")
    header = json.loads(bytes(data[8:8 + length]))
    start, out = 8 + length, {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        dtype = getattr(torch, DTYPES[info["dtype"]])
        a, b = info["data_offsets"]
        flat = torch.frombuffer(data, dtype=dtype, count=(b - a) // dtype.itemsize if b > a else 0, offset=start + a) \
            if b > a else torch.empty(0, dtype=dtype)
        out[name] = flat.reshape(info["shape"]).to(device)
    torch.cuda.synchronize()
    del data
    return out

class TorchEngine:
    """YuE2's own PyTorch stages, kept loaded: plan, semantic tokens, flow matching, decoder."""

    def __init__(self, model_dir: Path, vae_dir: Path, src: Path | None, quant: str = "bf16", backend: str = "torch",
                 tf32_vae: bool = True) -> None:
        self.model_dir, self.vae_dir, self.src = model_dir, vae_dir, src
        self.quant, self.backend, self.tf32_vae = quant, backend, tf32_vae
        self.pipe = self.vae = None
        self.info: dict = {}

    def load(self) -> dict:
        import torch
        import yue2.pipeline as pipeline

        began = time.time()
        # the installer checked each checkpoint's sha256 against its pinned manifest and left a stamp of the size and
        # time it checked: read as the identity, a second hash of 7.3 GB at every load is not made
        hashed = pipeline.model_identity

        def identity(path, verify=True):
            path = Path(path)
            manifest = json.loads((path / "weights_manifest.json").read_text())
            files = manifest.get("files", {})
            stamp = (path / ".checked").read_text().split() if (path / ".checked").is_file() else []
            weight = path / "model.safetensors"
            if len(stamp) == 4 and list(files) == ["model.safetensors"] and stamp[1] == files["model.safetensors"]["sha256"] \
                    and stamp[2:] == [str(weight.stat().st_size), str(int(weight.stat().st_mtime))]:
                return {"files": files, "config_sha256": pipeline.sha256_file(path / "config.json")}
            return hashed(path, verify)

        pipeline.model_identity = identity
        self.pipe = pipeline.YuE2Pipeline(self.model_dir, self.vae_dir, device="cuda", memory_budget_gib=40,
                                          backend="torch", progress=False)
        from yue2.modeling_yue2 import YuE2ForCausalLM

        model = YuE2ForCausalLM.from_pretrained(self.model_dir, local_files_only=True, dtype=torch.bfloat16,
                                                low_cpu_mem_usage=True).eval()
        missing, unexpected = model.load_state_dict(read_weights(self.model_dir / "model.safetensors", "cuda"), strict=False,
                                                    assign=True)
        if missing or unexpected:  # a checkpoint laid out otherwise: YuE2's own loading
            note(f"weights read the slow way ({len(missing)} missing, {len(unexpected)} unexpected names)")
            model = None
        else:
            self.pipe._model = model.to("cuda")
        model = self.pipe._load_model()
        from yue2.modeling_vae import YuE2VAE
        self.vae = YuE2VAE.from_pretrained(self.vae_dir, decoder_only=True, device="cuda", local_files_only=True)
        self.info = {"weights": self.quant, "backend": self.backend}
        if self.backend == "blocks":
            # TensorFold's decode engine (spark/tensorfold_yue2, beside this file: -I leaves this folder off the path)
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.append(here)
            import tensorfold_yue2

            self.info.update(tensorfold_yue2.prepare(model, quant=self.quant) or {})
            if self.info.get("decode") != "tensorfold-cuda":
                note(f"decoding in PyTorch: {self.info.get('decode_reason', 'no engine')}")
                if self.quant == "int8":  # no engine here: the int8 token loop as with --backend torch
                    self.info.update(int8_linears(model))
        elif self.quant == "int8":
            self.info.update(int8_linears(model))
        torch.cuda.synchronize()
        self.info["load_s"] = round(time.time() - began, 1)
        self.info["device_gb"] = round(torch.cuda.memory_allocated() / 1e9, 1)
        note(f"weights on the device: {self.info['device_gb']} GB")
        return self.info

    def song(self, request: dict, progress) -> dict:
        import numpy as np
        import soundfile as sf
        import torch
        from yue2.nar import synthesize
        from yue2.protocol import SongRequest

        pipe, began = self.pipe, time.time()
        torch.cuda.reset_peak_memory_stats()
        seconds, seed = float(request.get("seconds") or 60), int(request.get("seed") or 1)
        style, lyrics = str(request["style"]), str(request.get("lyrics") or "")
        instrumental = bool(request.get("instrumental"))
        cot = request.get("cot") or "full"
        tokens = {"plan": 0, "semantic": 0}

        def counted(phase, token):
            key = "plan" if phase == "abc" else "semantic"
            tokens[key] += 1
            if tokens[key] % 25 == 1:
                total = 4096 if key == "plan" else budget(seconds)
                progress(key, tokens[key], total)

        stamp = {}
        if instrumental:  # the skill's instrumental workflow: plan from tags, Vocal notes to Ins, render that score
            style = instrumental_style(style)
            asked = SongRequest(id="plan", style=style, lyrics=plan_lyrics(lyrics, seconds), cot="full", seed=seed)
            first = pipe.plan(request=asked, on_token=counted)
            if first.truncated or not first.abc:
                raise RuntimeError("YuE2's score for the instrumental came out empty or cut short; try another seed")
            converted = self.instrumental_score(first.abc)
            from abc_tools import parse_abc

            cot = "full" if parse_abc(converted).voices["Vocal"].chords else "melody"
            plan = pipe.plan(request=SongRequest(id="song", style=style, lyrics=lyric_tags(converted), cot=cot, seed=seed,
                                                 abc=converted))
            plan.timing = first.timing
        else:
            plan = pipe.plan(request=SongRequest(id="song", style=style, lyrics=lyrics, cot=cot, seed=seed), on_token=counted)
        stamp["plan"] = time.time()
        semantic = pipe.generate_semantic(plan, sampling={"max_tokens": budget(seconds),
                                                          "min_tokens": min(200, budget(seconds) - 1)},
                                          on_token=counted)
        stamp["semantic"] = time.time()
        model = pipe._model
        latents = synthesize(model, plan.prefix, semantic.tokens, seed, steps=pipe.generation_config.ode_steps,
                             context=pipe.generation_config.context,
                             on_progress=lambda done, total: progress("nar", done, total)
                             if done % 4 == 0 or done == total else None).float().numpy()
        stamp["nar"] = time.time()
        audio = self.decode(latents, progress)
        stamp["vae"] = time.time()
        cut = bool(semantic.truncated)
        if cut:
            fade_out(audio)
        wav = Path(request["wav"])
        wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(wav), audio, RATE, subtype="PCM_24")
        score = None
        if request.get("artifacts"):  # the score and what it was made from, beside the song
            folder = Path(request["artifacts"])
            folder.mkdir(parents=True, exist_ok=True)
            plan.save(folder)
            np.save(folder / "semantic.npy", np.asarray(semantic.tokens, dtype=np.int32))
            np.save(folder / "latent.npy", latents.astype(np.float32))
            (folder / "settings.json").write_text(json.dumps({
                "request": plan.request.to_dict(), "seconds": seconds, "max_semantic_tokens": budget(seconds),
                "instrumental": instrumental, "truncated": {"abc": bool(plan.truncated), "semantic": cut},
                "config": pipe.effective_config(plan.request), "engine": self.info, "weights": pipe.weights}, indent=1) + "\n")
            score = str(folder / "score.abc") if plan.abc is not None else None
        audio_s = len(audio) / RATE
        total = time.time() - began
        return {"ok": True, "audio_s": round(audio_s, 2), "ended": not cut, "truncated": {"abc": bool(plan.truncated), "semantic": cut},
                "plan_ms": round((stamp["plan"] - began) * 1000), "semantic_ms": round((stamp["semantic"] - stamp["plan"]) * 1000),
                "nar_ms": round((stamp["nar"] - stamp["semantic"]) * 1000), "vae_ms": round((stamp["vae"] - stamp["nar"]) * 1000),
                "total_ms": round(total * 1000), "rtf": round(total / audio_s, 3) if audio_s else None,
                "tokens": {**tokens, "plan_tps": round(tokens["plan"] / max(stamp["plan"] - began, 1e-6), 1),
                           "semantic_tps": round(tokens["semantic"] / max(stamp["semantic"] - stamp["plan"], 1e-6), 1)},
                "peak_memory_gb": round(torch.cuda.max_memory_reserved() / 1e9, 2), "score": score, "cot": plan.request.cot,
                "instrumental": instrumental, "sample_rate": RATE, "weights": self.quant}

    def instrumental_score(self, abc: str) -> str:
        """Every Vocal note moved to Ins (yue2-music's instrumental helpers, from the installed source)."""

        folder = (self.src / "skills" / "yue2-music" / "instrumental" / "scripts") if self.src else None
        if folder is None or not (folder / "instrumentalize.py").is_file():
            raise RuntimeError(f"no yue2-music instrumental helpers under {self.src} (bash spark/setup-yue2.sh)")
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        from instrumentalize import convert_score

        converted, _ = convert_score(abc, overlap="vocal", keep_chords=True)
        return converted

    def decode(self, latents, progress):
        """The decoder kept on the GPU between songs (YuE2's own decode moves models to the CPU and back)."""

        import torch

        z = torch.as_tensor(latents, dtype=torch.float32).T.unsqueeze(0)
        tiles = (z.shape[-1] + 1023) // 1024
        was = torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32
        torch.backends.cudnn.allow_tf32 = torch.backends.cuda.matmul.allow_tf32 = self.tf32_vae
        try:
            with torch.inference_mode():
                audio = self.vae.decode_tiled(z, core_frames=1024, halo_frames=16, output_device="cpu",
                                              on_progress=lambda done, total: progress("vae", done, total))
        finally:
            torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32 = was
        if not torch.isfinite(audio).all():
            raise ValueError("the decoder made non-finite audio")
        progress("vae", tiles, tiles)
        return audio[0].float().clamp(-1, 1).T.contiguous().numpy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--vae", type=Path, required=True)
    parser.add_argument("--src", type=Path, help="the YuE source checkout (its yue2-music instrumental helpers)")
    parser.add_argument("--quant", choices=("bf16", "int8"), default="bf16")
    parser.add_argument("--backend", choices=("torch", "blocks"), default="torch")
    parser.add_argument("--no-tf32-vae", action="store_true", help="the decoder in strict fp32 (YuE2's own setting)")
    parser.add_argument("--serve", action="store_true", help="the persistent server (the default)")
    args = parser.parse_args()
    engine = TorchEngine(args.model, args.vae, args.src, args.quant, args.backend, not args.no_tf32_vae)
    try:
        info = engine.load()
    except Exception as error:  # noqa: BLE001 - said on the protocol, then the process ends
        say({"ok": False, "error": f"{type(error).__name__}: {error}"[-600:]})
        return 1
    say({"ready": True, "engine": "yue2-torch", **info, "sample_rate": RATE, "channels": 2, "stream": False})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            say({"ok": False, "error": "a request is one JSON object a line"})
            continue
        job = str(request.get("id") or "")

        def progress(stage, done, total, job=job):
            note(f"{stage} step {int(done)}/{int(total)}")
            if request.get("progress"):
                say({"id": job, "progress": {"stage": stage, "done": int(done), "total": int(total)}})

        try:
            answer = engine.song(request, progress)
        except Exception as error:  # noqa: BLE001 - one song failed, the server stays
            import traceback

            note("failed: " + traceback.format_exc(limit=3).replace("\n", " | ")[-800:])
            answer = {"ok": False, "error": f"{type(error).__name__}: {error}"[-600:]}
        say({"id": job, **answer})
        if not answer.get("ok", True):
            # what the failed song held on the GPU goes back before the next one (an out-of-memory most of all): here,
            # once the error and its frames are gone, so the song's tensors can be collected
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001 - no torch or no GPU: nothing to give back
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
