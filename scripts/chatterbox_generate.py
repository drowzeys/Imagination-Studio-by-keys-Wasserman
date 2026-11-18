"""One Chatterbox Turbo take for the Studio queue. The model is loaded only for this job."""

from __future__ import annotations

import argparse
import json
import os
import time
import wave
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--text-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--temperature", type=float, required=True)
    parser.add_argument("--top-p", type=float, required=True)
    parser.add_argument("--top-k", type=int, required=True)
    args = parser.parse_args()

    # A Studio job owns the GPU lock and memory reservation before this process starts.
    os.environ["HF_HUB_OFFLINE"] = "1"
    started = time.monotonic()
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    if not torch.cuda.is_available():
        raise RuntimeError("Chatterbox Turbo needs a working CUDA driver on this machine")
    torch.manual_seed(args.seed)
    model = ChatterboxTurboTTS.from_local(args.model, device="cuda")
    loaded = time.monotonic()
    text = args.text_file.read_text().strip()
    with torch.inference_mode():
        audio = model.generate(text, audio_prompt_path=str(args.reference) if args.reference else None,
                               temperature=args.temperature, top_p=args.top_p, top_k=args.top_k)
    samples = audio.squeeze().detach().cpu().float().clamp(-1, 1).numpy()
    if len(samples) == 0:
        raise RuntimeError("Chatterbox Turbo returned no audio")
    pcm = (samples * 32767).astype("<i2")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(model.sr)
        output.writeframes(pcm.tobytes())
    ended = time.monotonic()
    args.stats.write_text(json.dumps({"audio_s": round(len(samples) / model.sr, 3),
                                      "load_s": round(loaded - started, 3), "engine_s": round(ended - loaded, 3),
                                      "total_s": round(ended - started, 3)}, indent=1) + "\n")


if __name__ == "__main__":
    main()
