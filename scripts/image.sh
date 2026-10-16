#!/bin/bash
# Make one image with Qwen-Image-2.1-Turbo on TensorFold.
#   bash scripts/image.sh "a prompt" out.png
#   TURBO=0 bash scripts/image.sh "a prompt" out.png        # the base model, 40 steps (needs QWEN_BASE_DIR)
#   PROMPT_FILE=prompts/baker-image.txt bash scripts/image.sh "" out.png
#   IMAGE_MODEL=flux3k-fast bash scripts/image.sh "a prompt" out.png   # FLUX3-K Fast (16 steps); flux3k-full: 50 steps
# Environment: PREFIX, QWEN_MODEL_DIR (Qwen-Image-2.1-Turbo: 8 steps on the schedule saved with the checkpoint),
# QWEN_BASE_DIR (Qwen-Image-2.1, for TURBO=0), TURBO, STEPS (base model only, default 40), WIDTH, HEIGHT (multiples
# of 16), SEED, EXTRA (extra flags). An install from before 2.3 has the base model at QWEN_MODEL_DIR: it keeps
# working as it did, with the Viggle turbo adapter on its six nodes. IMAGE_MODEL (qwen, the default; flux3k-fast;
# flux3k-full), FLUX3_MODEL_DIR (default ~/flux3-models) and UPSAMPLE choose and steer FLUX3-K.
set -euo pipefail
# On a DGX Spark (Linux, CUDA) the same interface is served by spark/image.sh.
[ "$(uname -s)" != Linux ] || exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/spark/image.sh" "$@"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
QWEN_MODEL_DIR="${QWEN_MODEL_DIR:-$HOME/qwen-models/Qwen-Image-2.1-Turbo}"
QWEN_BASE_DIR="${QWEN_BASE_DIR:-$HOME/qwen-models/Qwen-Image-2.1}"
[ -d "$QWEN_MODEL_DIR" ] || QWEN_MODEL_DIR="$QWEN_BASE_DIR"
TURBO_ADAPTER="${TURBO_ADAPTER:-$PREFIX/adapters/Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r256.safetensors}"
PROMPT="${1:-}"; OUT="${2:-outputs/image.png}"
mkdir -p "$(dirname "$OUT")"
export PATH="/opt/homebrew/bin:$PATH"
# IMAGE_MODEL=flux3k-fast | flux3k-full: FLUX3-K (Black Forest Labs' FLUX 3 Image Klein) through flux3-mlx in its own
# environment (oneshot-setup.sh --flux3). Fast is the distilled checkpoint (16 steps), Full the base one (50 steps);
# BFL's prompt upsampler writes the caption first (UPSAMPLE=0 sends a prompt that is already a full scene caption).
case "${IMAGE_MODEL:-qwen}" in
  flux3k-fast|flux3k-full)
    FLUX3_MODEL_DIR="${FLUX3_MODEL_DIR:-$HOME/flux3-models}"
    CKPT="$FLUX3_MODEL_DIR/$([ "$IMAGE_MODEL" = flux3k-fast ] && echo FLUX3-K-Fast || echo FLUX3-K-Full)"
    [ -x "$PREFIX/flux3-venv/bin/python" ] && [ -s "$CKPT/model.safetensors" ] \
      || { echo "FLUX3-K is not installed here (no $CKPT): bash oneshot-setup.sh --flux3" >&2; exit 1; }
    ARGS=("$CKPT" --model "$IMAGE_MODEL" -o "$OUT" --width "${WIDTH:-1344}" --height "${HEIGHT:-768}" --seed "${SEED:-0}")
    [ "${UPSAMPLE:-1}" = 0 ] || ARGS+=(--upsampler "$FLUX3_MODEL_DIR/FLUX3-K-pup-MLX-8bit")
    if [ -n "${PROMPT_FILE:-}" ]; then ARGS+=(--prompt-file "$PROMPT_FILE"); else ARGS+=(--prompt "$PROMPT"); fi
    # shellcheck disable=SC2086
    exec "$PREFIX/flux3-venv/bin/python" "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/flux3_generate.py" "${ARGS[@]}" ${EXTRA:-};;
  qwen) ;;
  *) echo "IMAGE_MODEL is qwen, flux3k-fast or flux3k-full, not ${IMAGE_MODEL}" >&2; exit 2;;
esac
# the Turbo checkpoint carries its schedule in model_index.json; the base model does not
if grep -q sample_sigmas "$QWEN_MODEL_DIR/model_index.json" 2>/dev/null; then DISTILLED=1; else DISTILLED=0; fi
SIZE=(-o "$OUT" --width "${WIDTH:-1344}" --height "${HEIGHT:-768}" --seed "${SEED:-0}")
if [ "${TURBO:-1}" = 1 ] && [ "$DISTILLED" = 1 ]; then
  ARGS=("$QWEN_MODEL_DIR" "${SIZE[@]}" --nodes 1.0,0.978453,0.95418,0.926626,0.89508,0.845148,0.704534,0.414568)
elif [ "${TURBO:-1}" = 1 ]; then
  ARGS=("$QWEN_MODEL_DIR" "${SIZE[@]}" --lora "$TURBO_ADAPTER" --nodes 1.0,0.9375,0.875,0.75,0.5,0.25)
else
  [ "$DISTILLED" = 0 ] || QWEN_MODEL_DIR="$QWEN_BASE_DIR"
  [ -d "$QWEN_MODEL_DIR/transformer" ] || { echo "TURBO=0 needs the base model: hf download Qwen/Qwen-Image-2.1 --local-dir $QWEN_BASE_DIR" >&2; exit 1; }
  ARGS=("$QWEN_MODEL_DIR" "${SIZE[@]}" --steps "${STEPS:-40}")
fi
if [ -n "${PROMPT_FILE:-}" ]; then ARGS+=(--prompt-file "$PROMPT_FILE"); else ARGS+=(--prompt "$PROMPT"); fi
# shellcheck disable=SC2086
exec "$PREFIX/venv/bin/python" "$PREFIX/qwen_image_generate.py" "${ARGS[@]}" ${EXTRA:-}
