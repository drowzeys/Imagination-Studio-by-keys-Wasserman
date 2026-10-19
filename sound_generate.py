#!/usr/bin/env python3
"""Sound effects and foley for Imagination Studio (app/sound.py queues these; scripts/setup-sound.sh installs them).

    python sound_generate.py sfx   --models M --prompt "rain on a tin roof" --seconds 8 -o out.wav --take out.take.json
    python sound_generate.py foley --models M --video clip.mp4 [--prompt "footsteps on gravel"] -o out.wav --clip-out out.mp4

sfx is Stability AI's Stable Audio Open 1.0 through diffusers' StableAudioPipeline (44.1 kHz stereo, up to 47 s).
foley is MMAudio large_44k_v2 (Ho Kei Cheng et al., code MIT, weights CC-BY-NC 4.0), as its demo.py runs it at the
commit setup-sound.sh pins, with the weights in M/mmaudio rather than the working folder; the sound is then laid
under the clip's own sound (its speech stays, peaks under -1 dBFS) or in place of it, with ffmpeg.
Runs in $PREFIX/sound-venv only, on Metal (MPS) in float32, else CUDA, else the processor.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIMIT = "alimiter=limit=0.891:level=false:attack=1:release=50"  # -1 dBFS


def say(text: str) -> None:
    print(f"[sound] {text}", flush=True)


def device_of():
    import torch
    if torch.cuda.is_available():
        return "cuda", torch.bfloat16
    if torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


def write_wav(path: Path, audio, rate: int) -> None:
    """audio: channels x samples, floats; written as 16-bit PCM, never past full scale."""

    import numpy as np
    import soundfile
    data = np.asarray(audio, dtype=np.float32)
    data = data[None, :] if data.ndim == 1 else data
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0.891:
        data = data * (0.891 / peak)
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(path), data.T, rate, subtype="PCM_16")


def seconds_of(video: Path) -> float:
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)],
                          capture_output=True, text=True, timeout=60)
    return float(done.stdout.strip() or 0)


def has_sound(video: Path) -> bool:
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True, timeout=60)
    return bool(done.stdout.strip())


def sfx(args) -> dict:
    import torch
    from diffusers import StableAudioPipeline
    device, dtype = device_of()
    folder = Path(args.models) / "stable-audio-open-1.0"
    say(f"Stable Audio Open on {device}: loading")
    pipe = StableAudioPipeline.from_pretrained(str(folder), torch_dtype=torch.float16 if device == "cuda" else torch.float32).to(device)
    generator = torch.Generator("cpu").manual_seed(args.seed)
    say(f"{args.steps} passes for {args.seconds:g} s")
    out = pipe(args.prompt, negative_prompt=args.negative or None, num_inference_steps=args.steps, guidance_scale=args.cfg,
               audio_end_in_s=args.seconds, num_waveforms_per_prompt=1, generator=generator)
    audio = out.audios[0]
    audio = audio.float().cpu().numpy() if hasattr(audio, "cpu") else audio
    rate = int(pipe.vae.sampling_rate)
    write_wav(Path(args.output), audio, rate)
    return {"model": "Stable Audio Open 1.0", "rate": rate, "seconds": args.seconds}


def foley(args) -> dict:
    import torch
    from mmaudio.eval_utils import ModelConfig, generate, load_video
    from mmaudio.model.flow_matching import FlowMatching
    from mmaudio.model.networks import get_my_mmaudio
    from mmaudio.model.utils.features_utils import FeaturesUtils
    device, dtype = device_of()
    root = Path(args.models) / "mmaudio"
    model = ModelConfig(model_name="large_44k_v2", model_path=root / "weights/mmaudio_large_44k_v2.pth", vae_path=root / "ext_weights/v1-44.pth",
                        bigvgan_16k_path=None, mode="44k", synchformer_ckpt=root / "ext_weights/synchformer_state_dict.pth")
    for need in (model.model_path, model.vae_path, model.synchformer_ckpt):
        if not need.is_file():
            raise SystemExit(f"MMAudio's {need.name} is not in {root}: run scripts/setup-sound.sh")
    video = Path(args.video)
    duration = min(seconds_of(video), args.max_seconds)
    if duration < 1:
        raise SystemExit("the clip is shorter than a second")
    seq_cfg = model.seq_cfg
    say(f"MMAudio large_44k_v2 on {device}: loading")
    with torch.inference_mode():
        net = get_my_mmaudio(model.model_name).to(device, dtype).eval()
        net.load_weights(torch.load(model.model_path, map_location=device, weights_only=True))
        rng = torch.Generator(device=device)
        rng.manual_seed(args.seed)
        fm = FlowMatching(min_sigma=0, inference_mode="euler", num_steps=args.steps)
        features = FeaturesUtils(tod_vae_ckpt=model.vae_path, synchformer_ckpt=model.synchformer_ckpt, enable_conditions=True,
                                 mode=model.mode, bigvgan_vocoder_ckpt=model.bigvgan_16k_path, need_vae_encoder=False).to(device, dtype).eval()
        info = load_video(video, duration)
        seq_cfg.duration = info.duration_sec
        net.update_seq_lengths(seq_cfg.latent_seq_len, seq_cfg.clip_seq_len, seq_cfg.sync_seq_len)
        say(f"{args.steps} passes for {info.duration_sec:.2f} s of the clip")
        audios = generate(info.clip_frames.unsqueeze(0), info.sync_frames.unsqueeze(0), [args.prompt or ""], negative_text=[args.negative or ""],
                          feature_utils=features, net=net, fm=fm, rng=rng, cfg_strength=args.cfg)
    audio = audios.float().cpu()[0].numpy()
    write_wav(Path(args.output), audio, seq_cfg.sampling_rate)
    lay(video, Path(args.output), Path(args.clip_out), args.replace, args.foley_db)
    return {"model": "MMAudio large_44k_v2", "rate": seq_cfg.sampling_rate, "seconds": round(info.duration_sec, 3)}


def lay(video: Path, sound: Path, target: Path, replace: bool, level: float) -> None:
    """The clip with the foley: under its own sound (the speech stays on top), or in place of it; peaks under -1 dBFS."""

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not installed: the foley WAV is made, the clip with it is not")
    own = has_sound(video) and not replace
    mix = (f"[1:a]volume={level:g}dB[f];[0:a][f]amix=inputs=2:duration=first:normalize=0,{LIMIT}[a]" if own
           else f"[1:a]{LIMIT}[a]")
    part = target.with_name(target.stem + ".part.mp4")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(sound), "-filter_complex", mix, "-map", "0:v:0", "-map", "[a]",
                           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(part)],
                          capture_output=True, text=True, timeout=600)
    if done.returncode or not part.is_file():
        part.unlink(missing_ok=True)
        raise SystemExit("ffmpeg could not lay the foley under the clip: " + done.stderr.strip()[-300:])
    part.replace(target)
    say("laid " + ("under the clip's own sound" if own else "as the clip's sound"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("kind", choices=("sfx", "foley"))
    parser.add_argument("--models", required=True)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--negative", default="")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--cfg", type=float)
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--video")
    parser.add_argument("--max-seconds", type=float, default=15.0)
    parser.add_argument("--foley-db", type=float, default=-6.0)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--clip-out")
    parser.add_argument("--take")
    parser.add_argument("--home")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    args.steps = args.steps or (100 if args.kind == "sfx" else 25)
    args.cfg = args.cfg if args.cfg is not None else (7.0 if args.kind == "sfx" else 4.5)
    if args.kind == "foley" and not (args.video and args.clip_out):
        parser.error("foley takes --video and --clip-out")
    started = time.time()
    made = sfx(args) if args.kind == "sfx" else foley(args)
    if args.take:
        home = Path(args.home) if args.home else None
        rel = lambda p: str(Path(p).resolve().relative_to(home.resolve())) if home and p else (str(p) if p else None)
        Path(args.take).write_text(json.dumps({"id": Path(args.output).stem, "kind": args.kind, "prompt": args.prompt, "negative": args.negative,
                                               "seed": args.seed, "steps": args.steps, "cfg": args.cfg, "path": rel(args.output),
                                               "clip": rel(args.clip_out), "source": rel(args.video), "created": time.time(),
                                               "elapsed": round(time.time() - started, 1), **made}, indent=1))
    say(f"done in {time.time() - started:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
