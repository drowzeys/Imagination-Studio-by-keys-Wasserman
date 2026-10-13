#!/bin/bash
# Edit a picture with Qwen-Image-2.1: the picture goes in as the model's reference, the prompt says what changes.
#   bash scripts/edit.sh photo.png "make it night" out.png
#   SET_FILE=edits.json bash scripts/edit.sh photo.png        # several edits from one load: [[prompt, output], ...]
#   LORA=/path/angles.safetensors:0.9 bash scripts/edit.sh photo.png "<mva> back view, eye-level shot" back.png
#   IMAGES="person.png" bash scripts/edit.sh room.png "the person of image 2 sits on the sofa of image 1" out.png
# Environment: PREFIX, QWEN_MODEL_DIR (Qwen-Image-2.1-Turbo, its own 8 steps), BASE=1 (the 40-step base model at
# QWEN_BASE_DIR: oneshot-setup.sh --image-edit; what image LoRAs are trained on), STEPS (base model only), SEED,
# IMAGES (up to nine further pictures, one path a line: the prompt calls the edited picture image 1 and these
# image 2, image 3, ...), SIZE (source, the default: the result has the picture's own size; model: about RESOLUTION
# pixels squared, default 1024, keeping its shape), WIDTH and HEIGHT (another size for the result), LORA (one PATH or
# PATH:STRENGTH a line), QUANTIZE (bits for the weights on a Mac: 8 on a 64 GB Mac; unset keeps bfloat16, 43 GiB at
# its peak with one picture), MOST_PIXELS (with SIZE=source: the most pixels an edit is made at; a larger picture is
# made at this many, in its own shape. Unset is 1536x1536; the app sets 1280x1280 on a Mac under 96 GB).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# On a DGX Spark (Linux, CUDA) the same interface is served by spark/edit.sh.
[ "$(uname -s)" != Linux ] || exec bash "$HERE/../spark/edit.sh" "$@"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
QWEN_MODEL_DIR="${QWEN_MODEL_DIR:-$HOME/qwen-models/Qwen-Image-2.1-Turbo}"
QWEN_BASE_DIR="${QWEN_BASE_DIR:-$(dirname "$QWEN_MODEL_DIR")/Qwen-Image-2.1}"
IMAGE="${1:-}"; PROMPT="${2:-}"; OUT="${3:-}"
[ -f "$IMAGE" ] || { echo "give the picture to edit: bash scripts/edit.sh photo.png \"what changes\" out.png" >&2; exit 1; }
MODEL="$QWEN_MODEL_DIR"
if [ "${BASE:-0}" = 1 ]; then
  MODEL="$QWEN_BASE_DIR"
  [ -d "$MODEL/transformer" ] || { echo "BASE=1 needs the base model: bash oneshot-setup.sh --image-edit (Qwen-Image-2.1, 13 GB more; Qwen Research License, non-commercial)" >&2; exit 1; }
fi
[ -f "$MODEL/processor/preprocessor_config.json" ] || [ -d "$MODEL/processor" ] || { echo "$MODEL has no processor folder: editing needs the whole checkpoint (text encoder with its eyes, processor, both halves of the image coder)" >&2; exit 1; }
export PATH="/opt/homebrew/bin:$PATH"
ARGS=("$MODEL" --image "$IMAGE" --seed "${SEED:-0}" --size "${SIZE:-source}" --resolution "${RESOLUTION:-1024}" --steps "${STEPS:-40}")
if [ -n "${IMAGES:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--image "$ONE"); done <<<"$IMAGES"
fi
[ -z "${WIDTH:-}" ] || [ -z "${HEIGHT:-}" ] || ARGS+=(--width "$WIDTH" --height "$HEIGHT")
[ -z "${MOST_PIXELS:-}" ] || ARGS+=(--most "$MOST_PIXELS")
if [ -n "${SET_FILE:-}" ]; then ARGS+=(--set "@$SET_FILE"); else ARGS+=(--prompt "$PROMPT" -o "$OUT"); fi
[ -z "${QUANTIZE:-}" ] || ARGS+=(--quantize "$QUANTIZE")
if [ -n "${LORA:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--lora "$ONE"); done <<<"$LORA"
fi
exec "$PREFIX/venv/bin/python" "$HERE/../qwen_image_edit.py" "${ARGS[@]}"
