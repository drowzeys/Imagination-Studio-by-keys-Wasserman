"""Checks the TensorFold YuE2 engine against the package's PyTorch decode on a GPU, and times both.

    python -m tensorfold_yue2.check parity --model DIR --vae DIR --weights int8 --out parity.json
    python -m tensorfold_yue2.check bench  --model DIR --vae DIR --weights int8 --out bench.json
    python -m tensorfold_yue2.check song   --model DIR --vae DIR --engine int8 --seconds-out song.flac --out song.json

parity: teacher forcing. The reference's own CUDA-graph decode draws each token (the package's distribution and
seeded multinomial); the engine is fed the same tokens from the same prompt pass, and every step's logits are
compared on the rows the phase can draw (cosine, largest difference, greedy agreement, top-50 overlap, KL of the
phase's sampling distributions, the drawn token's rank), then every layer's written keys and values, and the residual
stream after each layer for one step. bench: decode steps a second for both at several context lengths, and a whole
semantic generation with sampling. song: a whole song through YuE2Pipeline with the engine installed (or not).
The request is an invented one; no user material is read.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

STYLE = "Indie pop, warm female vocal, acoustic guitar, soft drums, bright, hopeful"
LYRICS = ("[Verse]\nMorning light on the window pane\nCoffee cooling while I wait for rain\n"
          "Every street I know has a different name\nAnd I walk them all the same\n\n"
          "[Chorus]\nHold on, the day is new\nHold on, the sky is blue\nEverything I need is here with you\n\n"
          "[Verse]\nPaper boats along the gutter line\nLittle wishes that I left behind\n"
          "Every road I take comes back to mine\nAnd it's fine, it's fine\n\n"
          "[Chorus]\nHold on, the day is new\nHold on, the sky is blue\nEverything I need is here with you\n")


def neighbours():
    """Other GPU work on the machine while timing (the governor's other reservations and other CUDA processes): a
    timing taken beside another job is marked, not trusted."""
    import os
    import subprocess
    seen = {"reservations": [], "processes": []}
    # a workload governor's admission client, where the machine has one (SPARK_GOVERNOR names it; none is asked
    # otherwise), and the owner prefix of this check's own reservations (SPARK_GOVERNOR_SELF), which are not neighbours
    governor = os.environ.get("SPARK_GOVERNOR", "").strip()
    mine = os.environ.get("SPARK_GOVERNOR_SELF", "tensorfold-yue2").strip()
    if not governor:
        seen["reservations"] = ["not asked"]  # no governor named: not the same as a quiet machine
    else:
        try:
            status = json.loads(subprocess.run(["python3", governor, "--owner", "probe", "--status"], capture_output=True,
                                               text=True, timeout=20).stdout)
            seen["reservations"] = sorted(r["owner"] for r in status.get("reservations", [])
                                          if not (mine and str(r["owner"]).startswith(mine)))
        except Exception:
            seen["reservations"] = ["unknown"]
    try:
        rows = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"], capture_output=True,
                              text=True, timeout=20).stdout.split()
        seen["processes"] = sorted(int(p) for p in rows if p.isdigit() and int(p) != os.getpid())
    except Exception:
        pass
    return seen


def load(args):
    import torch
    from yue2.pipeline import YuE2Pipeline
    pipe = YuE2Pipeline(args.model, args.vae, verify_hashes=False, progress=False, memory_budget_gib=args.budget)
    model = pipe._load_model()
    torch.cuda.synchronize()
    return pipe, model


def prefixes(pipe, phase, cfg_pair=False, seed=7, abc_tokens=400):
    """The phase's prompt ids for the invented request; the semantic phase's score comes from a short plan."""
    from yue2.protocol import SongRequest, token_prefixes, negative_prefix, Sampling
    request = SongRequest(style=STYLE, lyrics=LYRICS, seed=seed)
    if phase == "abc":
        return [token_prefixes(request, pipe.tokenizer)], request
    plan = pipe.plan(request=request, abc_sampling=Sampling(.7, .9, 30, 1.005, 100, 32, abc_tokens))
    prefix = plan.prefix
    if cfg_pair:
        return [prefix, negative_prefix(request, pipe.tokenizer, plan.abc_ids)], request
    return [prefix], request


def allowed(phase, vocab):
    from yue2.protocol import EOD, ABC_END, MUSIC_END, CODEC_OFFSET, CODEC_SIZE
    import torch
    if phase == "abc":
        return torch.cat([torch.arange(EOD), torch.tensor([ABC_END])])
    return torch.cat([torch.tensor([MUSIC_END]), torch.arange(CODEC_OFFSET, CODEC_OFFSET + CODEC_SIZE)])


def compare_logits(ref, eng, rows, sampling, history, step, phase, mixed_ref, mixed_eng):
    import torch
    from yue2.sampling import distribution
    r, e = ref.float()[..., rows], eng.float()[..., rows]
    cos = torch.nn.functional.cosine_similarity(r, e, dim=-1)
    top_r = r.topk(50, dim=-1).indices
    top_e = e.topk(50, dim=-1).indices
    overlap = [len(set(a.tolist()) & set(b.tolist())) / 50 for a, b in zip(top_r, top_e)]
    pr = distribution(mixed_ref[:1], sampling, history, step, phase).softmax(-1)
    pe = distribution(mixed_eng[:1], sampling, history, step, phase).softmax(-1)
    mask = pr > 0
    kl = float((pr[mask] * (pr[mask].log() - pe[mask].clamp_min(1e-30).log())).sum())
    tv = float(0.5 * (pr - pe).abs().sum())
    return {"cos": cos.tolist(), "max_abs": float((r - e).abs().max()), "greedy": (r.argmax(-1) == e.argmax(-1)).tolist(),
            "top50": overlap, "kl": kl, "tv": tv}


def parity(args):
    import torch
    import tensorfold_yue2 as tf
    from yue2.cuda_graph import GraphAR
    from yue2.protocol import CODEC_OFFSET
    from yue2.sampling import distribution
    pipe, model = load(args)
    if args.against == "torch-sdpa":
        # the noise floor: the package's own eager decode (public SDPA) against its CUDA-graph decode
        engine = None
        def TF(model, prefs, n, **kw):
            return GraphAR(model, prefs, n, capture=False, attention_backend="sdpa")
        report = {"weights": "torch-sdpa", "cases": []}
    else:
        engine = tf.Engine(model, weights=args.weights, splits=args.splits)
        TF = tf.graph_ar_class(engine, GraphAR)
        report = {"weights": args.weights, "engine": engine.info(), "cases": []}
    cases = [("abc", False), ("semantic", False), ("semantic", True)]
    for phase, pair in cases:
        prefs, request = prefixes(pipe, phase, pair, args.seed)
        sampling = pipe.generation_config.abc if phase == "abc" else pipe.generation_config.semantic
        n = args.steps
        ref = GraphAR(model, prefs, n + 1)
        mine = TF(model, prefs, n + 1)
        with torch.inference_mode():
            lr, le = ref.prefill(), mine.prefill()
            generator = torch.Generator(device="cuda").manual_seed(request.seed)
            rows = allowed(phase, model.config.vocab_size).cuda()
            history, steps, ranks = [], [], []
            cfg = 1.5 if pair else 1.0
            for i in range(n):
                logits_r = lr if not pair else lr[1:] + cfg * (lr[:1] - lr[1:])
                logits_e = le if not pair else le[1:] + cfg * (le[:1] - le[1:])
                if i:   # the prompt pass is the same code in both; only decoded rows are the engine's
                    steps.append(compare_logits(lr, le, rows, sampling, history, i, phase, logits_r, logits_e))
                scores = distribution(logits_r[:1], sampling, history, i, phase)
                token = torch.multinomial(scores.softmax(-1), 1, generator=generator)
                tid = int(token.item())
                if i:
                    order = logits_e[0].float().argsort(descending=True)
                    ranks.append(int((order == tid).nonzero()[0, 0]) + 1)
                history.append(tid)
                lr = ref.step(token).clone()
                le = mine.step(token).clone()
            torch.cuda.synchronize()
            # every layer's keys and values the decode wrote
            start = [len(p) for p in prefs]
            kv = []
            for layer, (kr, ke, vr, ve) in enumerate(zip(ref.keys, mine.keys, ref.values, mine.values)):
                k_err, v_err = [], []
                for b, s in enumerate(start):
                    a, c = kr[b, s:s + n].float(), ke[b, s:s + n].float()
                    k_err.append(float((a - c).norm() / a.norm()))
                    a, c = vr[b, s:s + n].float(), ve[b, s:s + n].float()
                    v_err.append(float((a - c).norm() / a.norm()))
                kv.append({"layer": layer, "k_rel": max(k_err), "v_rel": max(v_err)})
        ref.close()
        mine.close()
        def mean(key, reduce=lambda v: sum(v) / len(v)):
            vals = [reduce(s[key]) if isinstance(s[key], list) else s[key] for s in steps]
            return sum(vals) / len(vals)
        report["cases"].append({
            "phase": phase, "cfg_pair": pair, "prefix_tokens": [len(p) for p in prefs], "steps": n,
            "cos_mean": mean("cos"), "cos_min": min(min(s["cos"]) for s in steps),
            "max_abs_max": max(s["max_abs"] for s in steps),
            "greedy_agreement": mean("greedy", lambda v: sum(v) / len(v)),
            "top50_overlap": mean("top50"), "kl_mean": mean("kl"), "kl_max": max(s["kl"] for s in steps),
            "tv_mean": mean("tv"), "drawn_rank_median": sorted(ranks)[len(ranks) // 2],
            "drawn_rank_1_share": sum(r == 1 for r in ranks) / len(ranks),
            "kv_rel_max": max(max(x["k_rel"], x["v_rel"]) for x in kv), "kv_layers": kv})
        print(json.dumps({k: v for k, v in report["cases"][-1].items() if k != "kv_layers"}), flush=True)
    if engine is not None:
        report["layers"] = layer_check(model, engine, TF, pipe, args)
        engine.close()
    Path(args.out).write_text(json.dumps(report, indent=1))


def layer_check(model, engine, TF, pipe, args):
    """The residual stream after each layer for one decoded token: the engine (layers=k) against PyTorch eager."""
    import torch
    from yue2.cuda_graph import GraphAR
    prefs, _ = prefixes(pipe, "abc", False, args.seed)
    ref = GraphAR(model, prefs, 4, capture=False)
    mine = TF(model, prefs, 4, capture=False)
    out = []
    with torch.inference_mode():
        ref.prefill()
        mine.prefill()
        token = 1000
        backbone = model.model
        ref.tokens.fill_(token)
        cos, sin = backbone.rotary_emb(ref.positions[:, None])
        x = backbone.embed_tokens(ref.tokens)
        config = model.config
        slots = ref.positions[:, None, None, None].expand(1, 1, config.num_key_value_heads, config.head_dim)
        hidden = []
        for layer, keys, values in zip(backbone.layers, ref.keys, ref.values):
            q, k, v = layer.self_attn.project_qkv(layer.input_layernorm(x), cos, sin)
            keys.scatter_(1, slots, k)
            values.scatter_(1, slots, v)
            visible = (ref.key_positions[None, :] <= ref.positions[:, None])[:, None, None, :]
            h = torch.nn.functional.scaled_dot_product_attention(q.transpose(1, 2), keys.transpose(1, 2),
                    values.transpose(1, 2), attn_mask=visible, enable_gqa=True).transpose(1, 2)
            x = x + layer.self_attn.o_proj(h.reshape(1, 1, -1))
            x = x + layer.mlp(layer.post_attention_layernorm(x))
            hidden.append(x[:, 0].clone())
        logits_ref = model.lm_head(backbone.norm(x))[:, 0]
        mine.tokens.fill_(token)
        p0 = mine.positions.clone()
        buf = torch.empty((1, config.hidden_size), dtype=torch.bfloat16, device="cuda")
        for k in range(1, config.num_hidden_layers + 1):
            mine.positions.copy_(p0)
            engine.step(mine.step_inputs(layers=k, hidden_out=buf.data_ptr()))
            torch.cuda.synchronize()
            a, b = hidden[k - 1].float(), buf.float()
            out.append({"layer": k, "rel": float((a - b).norm() / a.norm()), "max_abs": float((a - b).abs().max()),
                        "cos": float(torch.nn.functional.cosine_similarity(a, b, dim=-1))})
        mine.positions.copy_(p0)
        mine.row0, mine.row1 = 0, config.vocab_size
        engine.step(mine.step_inputs())
        torch.cuda.synchronize()
        a, b = logits_ref.float(), mine.logits.float()
        out.append({"logits_cos": float(torch.nn.functional.cosine_similarity(a, b, dim=-1)),
                    "logits_max_abs": float((a - b).abs().max())})
    ref.close()
    mine.close()
    print(json.dumps(out[-1]), flush=True)
    return out


def bench(args):
    import torch
    import tensorfold_yue2 as tf
    from yue2.cuda_graph import GraphAR
    pipe, model = load(args)
    engine = tf.Engine(model, weights=args.weights, splits=args.splits)
    TF = tf.graph_ar_class(engine, GraphAR)
    prefs, request = prefixes(pipe, "semantic", False, args.seed)
    out = {"weights": args.weights, "engine": engine.info(), "decode": [], "neighbours_before": neighbours()}
    with torch.inference_mode():
        for context in args.contexts:
            for name, cls in (("torch", GraphAR), ("tensorfold", TF)):
                pad = max(0, context - len(prefs[0]))
                prefix = prefs[0][:-1] + [prefs[0][0]] * pad + [prefs[0][-1]]
                g = cls(model, [prefix], args.decode + 2)
                g.prefill()
                token = torch.tensor([[160000]], device="cuda")
                for _ in range(5):
                    g.step(token)
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                for _ in range(args.decode - 5):
                    g.step(token)
                torch.cuda.synchronize()
                dt = time.perf_counter() - t0
                out["decode"].append({"backend": name, "context": len(prefix), "steps_per_s": (args.decode - 5) / dt,
                                      "ms_per_step": 1000 * dt / (args.decode - 5)})
                print(json.dumps(out["decode"][-1]), flush=True)
                g.close()
                del g
                torch.cuda.empty_cache()
        # whole semantic generations with the package's sampling, the same seed in both
        from yue2.sampling import generate_tokens
        from yue2.protocol import Sampling
        sampling = Sampling(1.0, .95, 100, 1.2, 50, args.semantic_tokens, args.semantic_tokens)
        runs = {}
        for name in ("torch", "tensorfold"):
            if name == "tensorfold":
                tf.install(engine)
            try:
                torch.cuda.synchronize()
                ids, timing, _ = generate_tokens(model, prefs[0], sampling, request.seed, "semantic")
            finally:
                tf.uninstall()
            runs[name] = ids
            out[f"semantic_{name}"] = {k: timing[k] for k in ("seconds", "prefill_seconds", "output_tokens", "output_tps", "attention")}
            print(json.dumps({name: out[f"semantic_{name}"]}), flush=True)
        a, b = runs["torch"], runs["tensorfold"]
        same = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        out["semantic_same_tokens_before_divergence"] = same
    out["peak_torch_gib"] = torch.cuda.max_memory_allocated() / 2**30
    out["neighbours_after"] = neighbours()
    engine.close()
    Path(args.out).write_text(json.dumps(out, indent=1))


def song(args):
    """Whole songs, one after another in one process (the model loaded once): `--engine torch,int8-groups` runs the
    same request on each in turn; `--repeat 2` runs the list twice."""
    import numpy as np
    import torch
    import tensorfold_yue2 as tf
    pipe, model = load(args)
    engines, results = {}, []
    for round_ in range(args.repeat):
        for name in args.engine.split(","):
            if name != "torch":
                if name not in engines:
                    # the pipeline moves its model to the CPU for the VAE: back on the GPU to copy from
                    engines[name] = tf.Engine(pipe._load_model(), weights=name, splits=args.splits)
                tf.install(engines[name])
            torch.cuda.reset_peak_memory_stats()
            before = neighbours()
            t0 = time.perf_counter()
            try:
                result = pipe(STYLE, LYRICS, seed=args.seed)
            finally:
                tf.uninstall()
            total = time.perf_counter() - t0
            audio = result.audio
            if args.audio_out and round_ == 0:
                path = Path(args.audio_out.replace("{engine}", name))
                result.save(path)
                np.save(path.with_suffix(".tokens.npy"), np.asarray(result.semantic.tokens, dtype=np.int32))
                np.save(path.with_suffix(".abc.npy"), np.asarray(result.semantic.plan.abc_ids, dtype=np.int32))
            seconds = len(audio) / result.sample_rate
            t = result.timing
            out = {"engine": name, "round": round_, "seed": args.seed, "audio_seconds": seconds, "total_seconds": total,
                   "rtf": total / seconds, "abc_tokens": t["abc"].get("output_tokens"), "abc_seconds": t["abc"].get("seconds"),
                   "abc_tps": t["abc"].get("output_tps"), "semantic_tokens": t["semantic"]["output_tokens"],
                   "semantic_seconds": t["semantic"]["seconds"], "semantic_tps": t["semantic"]["output_tps"],
                   "nar_seconds": t["nar_seconds"], "vae_seconds": t["vae_seconds"],
                   "peak_torch_gib": torch.cuda.max_memory_allocated() / 2**30,
                   "engine_weight_gib": engines[name].info()["weight_bytes"] / 2**30 if name in engines else 0,
                   "rms": float(np.sqrt((audio.astype(np.float64) ** 2).mean())),
                   "attention": t["semantic"].get("attention"), "neighbours_before": before, "neighbours_after": neighbours()}
            print(json.dumps(out), flush=True)
            results.append(out)
            Path(args.out).write_text(json.dumps(results, indent=1))
    for engine in engines.values():
        engine.close()


def nll(args):
    """The reference model's mean negative log-likelihood (teacher forced, bf16 prompt pass, the phase's allowed rows)
    of the score and song tokens each engine drew in `song` (--audio-out files): tokens drawn from the same model's
    distribution score alike; a decode that drifted would draw tokens the reference finds less likely."""
    import numpy as np
    import torch
    from yue2.protocol import SongRequest, token_prefixes, CODEC_OFFSET, MUSIC_END, ABC_END
    pipe, model = load(args)
    request = SongRequest(style=STYLE, lyrics=LYRICS, seed=args.seed)
    out = []
    for name in args.engine.split(","):
        base = Path(args.audio_out.replace("{engine}", name))
        abc = np.load(base.with_suffix(".abc.npy")).tolist()
        codec = np.load(base.with_suffix(".tokens.npy")).tolist()
        row = {"engine": name, "abc_tokens": len(abc), "semantic_tokens": len(codec)}
        with torch.inference_mode():
            for phase, prefix, drawn in (("abc", token_prefixes(request, pipe.tokenizer), abc + [ABC_END]),
                                         ("semantic", token_prefixes(request, pipe.tokenizer, abc),
                                          [c + CODEC_OFFSET for c in codec] + [MUSIC_END])):
                ids = torch.tensor([prefix + drawn], device="cuda")
                logits = model(ids, use_cache=False).logits[0, len(prefix) - 1:-1].float()
                rows = allowed(phase, model.config.vocab_size).cuda()
                logp = logits[:, rows].log_softmax(-1)
                index = torch.searchsorted(rows, torch.tensor(drawn, device="cuda"))
                picked = logp.gather(1, index[:, None])[:, 0]
                row[f"{phase}_nll"] = float(-picked.mean())
                row[f"{phase}_greedy_share"] = float((logp.argmax(-1) == index).float().mean())
        print(json.dumps(row), flush=True)
        out.append(row)
    Path(args.out).write_text(json.dumps(out, indent=1))


def longparity(args):
    """Teacher forcing over a whole song: a song's own score and semantic tokens (a `song --audio-out` run) fed to
    PyTorch's decode and the engine's, compared in windows of 256 positions: logits cosine, greedy agreement, the TV of
    the sampling distributions, and each decode's mean NLL of the forced tokens (raw logits, the phase's rows)."""
    import numpy as np
    import torch
    import tensorfold_yue2 as tf
    from yue2.cuda_graph import GraphAR
    from yue2.protocol import SongRequest, token_prefixes, CODEC_OFFSET
    from yue2.sampling import distribution
    pipe, model = load(args)
    engine = tf.Engine(model, weights=args.weights, splits=args.splits)
    TF = tf.graph_ar_class(engine, GraphAR)
    request = SongRequest(style=STYLE, lyrics=LYRICS, seed=args.seed)
    base = Path(args.audio_out)
    abc = np.load(base.with_suffix(".abc.npy")).tolist()
    codec = np.load(base.with_suffix(".tokens.npy")).tolist()
    prefix = token_prefixes(request, pipe.tokenizer, abc)
    forced = [c + CODEC_OFFSET for c in codec][: args.steps]
    sampling = pipe.generation_config.semantic
    rows = allowed("semantic", model.config.vocab_size).cuda()
    windows, cur = [], None
    with torch.inference_mode():
        ref, mine = GraphAR(model, [prefix], len(forced) + 1), TF(model, [prefix], len(forced) + 1)
        lr, le = ref.prefill(), mine.prefill()
        for i, tid in enumerate(forced):
            if i % 256 == 0:
                cur = {"from": len(prefix) + i, "n": 0, "cos": 0.0, "greedy": 0, "tv": 0.0, "nll_ref": 0.0, "nll_eng": 0.0}
                windows.append(cur)
            r, e = lr.float()[0, rows], le.float()[0, rows]
            k = int(torch.searchsorted(rows, torch.tensor(tid, device="cuda")))
            cur["n"] += 1
            cur["cos"] += float(torch.nn.functional.cosine_similarity(r, e, dim=0))
            cur["greedy"] += int(r.argmax() == e.argmax())
            hist = forced[max(0, i - sampling.penalty_window):i]
            pr = distribution(lr[:1], sampling, hist, i, "semantic").softmax(-1)
            pe = distribution(le[:1], sampling, hist, i, "semantic").softmax(-1)
            cur["tv"] += float(0.5 * (pr - pe).abs().sum())
            cur["nll_ref"] -= float(r.log_softmax(0)[k])
            cur["nll_eng"] -= float(e.log_softmax(0)[k])
            token = torch.tensor([[tid]], device="cuda")
            lr, le = ref.step(token).clone(), mine.step(token).clone()
        ref.close()
        mine.close()
    for w in windows:
        for key in ("cos", "greedy", "tv", "nll_ref", "nll_eng"):
            w[key] /= w["n"]
        print(json.dumps(w), flush=True)
    engine.close()
    Path(args.out).write_text(json.dumps({"weights": args.weights, "prefix": len(prefix), "windows": windows}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("what", choices=("parity", "bench", "song", "nll", "longparity"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--vae", required=True)
    parser.add_argument("--weights", default="int8", choices=sorted(__import__("tensorfold_yue2").WEIGHTS))
    parser.add_argument("--engine", default="int8", help="song: torch, bf16, int8 or int8-groups")
    parser.add_argument("--splits", type=int, default=32)
    parser.add_argument("--against", default="engine", choices=("engine", "torch-sdpa"))
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument("--decode", type=int, default=200)
    parser.add_argument("--contexts", type=int, nargs="+", default=[1000, 4000, 12000])
    parser.add_argument("--semantic-tokens", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--budget", type=float, default=40)
    parser.add_argument("--audio-out", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    {"parity": parity, "bench": bench, "song": song, "nll": nll, "longparity": longparity}[args.what](args)


if __name__ == "__main__":
    main()
