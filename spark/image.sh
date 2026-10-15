#!/bin/bash
# One image with Qwen-Image-2.1-Turbo on a DGX Spark (CUDA). scripts/image.sh hands over to this on Linux; same interface:
#   bash scripts/image.sh "a prompt" out.png
#   TURBO=0 bash scripts/image.sh "a prompt" out.png     # the base model, 40 steps, if you have downloaded it:
#     hf download Comfy-Org/Qwen-Image-2.1 diffusion_models/qwen_image_2.1_bf16.safetensors --local-dir "$SPARK_MODELS/Qwen-Image-2.1-Comfy"
# Environment: PREFIX, TURBO (1: Qwen-Image-2.1-Turbo on its own 8 steps; 0: base model), STEPS (base model only),
# WIDTH, HEIGHT (multiples of 16), SEED, PROMPT_FILE, EXTRA (--seeds 1,2,3 writes out_s1.png ...).
# IMAGE_MODEL=qwen-full is the base model as TURBO=0 runs it (Qwen 2.1 Full, 40 steps at CFG 4).
# LORA (one picture LoRA, PATH or PATH:STRENGTH, a line each; Qwen only; the base model, TURBO=0 or qwen-full, is what
# picture LoRAs are trained on).
# IMAGE_MODEL=flux3k-fast | flux3k-full makes it with FLUX3-K instead (oneshot-setup-spark.sh --flux3): Fast is the
# distilled checkpoint (16 steps), Full the base one (50 steps); BFL's prompt upsampler writes the caption first
# (UPSAMPLE=0 sends a prompt that is already a full scene caption as written).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
[ -x "$PREFIX/venv/bin/python" ] || { echo "run oneshot-setup-spark.sh first: no environment at $PREFIX" >&2; exit 1; }
PROMPT="${1:-}"; OUT="${2:-outputs/image.png}"
[ "${IMAGE_MODEL:-qwen}" != qwen-full ] || { export IMAGE_MODEL=qwen; TURBO=0; }
ARGS=(image -o "$OUT" --width "${WIDTH:-1344}" --height "${HEIGHT:-768}" --seed "${SEED:-0}")
[ "${TURBO:-1}" = 1 ] || ARGS+=(--base --steps "${STEPS:-40}")
[ "${UPSAMPLE:-1}" = 1 ] || ARGS+=(--no-upsample)
if [ -n "${PROMPT_FILE:-}" ]; then ARGS+=(--prompt-file "$PROMPT_FILE"); else ARGS+=(--prompt "$PROMPT"); fi
if [ -n "${LORA:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--lora "$ONE"); done <<<"$LORA"
fi
# shellcheck disable=SC2086
PREFIX="$PREFIX" exec "$PREFIX/venv/bin/python" "$HERE/spark_generate.py" "${ARGS[@]}" ${EXTRA:-}
