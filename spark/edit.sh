#!/bin/bash
# Edit a picture with Qwen-Image-2.1 on a DGX Spark (CUDA). scripts/edit.sh hands over to this on Linux; same interface:
#   bash scripts/edit.sh photo.png "make it night" out.png
#   SET_FILE=edits.json bash scripts/edit.sh photo.png        # several edits from one load: [[prompt, output], ...]
#   IMAGES="person.png" bash scripts/edit.sh room.png "the person of image 2 sits on the sofa of image 1" out.png
# Environment: PREFIX, BASE=1 (the 40-step base model: oneshot-setup-spark.sh --image-edit), STEPS (base model only),
# SEED, IMAGES (up to nine further pictures, one path a line: image 2, image 3, ... in the prompt), SIZE (source, the
# default: the result has the picture's own size; model: about RESOLUTION pixels squared), WIDTH and HEIGHT (another
# size for the result), LORA (one PATH or PATH:STRENGTH a line; merged at run time on the bfloat16 file).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
[ -x "$PREFIX/venv/bin/python" ] || { echo "run oneshot-setup-spark.sh first: no environment at $PREFIX" >&2; exit 1; }
IMAGE="${1:-}"; PROMPT="${2:-}"; OUT="${3:-}"
[ -f "$IMAGE" ] || { echo "give the picture to edit: bash scripts/edit.sh photo.png \"what changes\" out.png" >&2; exit 1; }
ARGS=(--image "$IMAGE" --seed "${SEED:-0}" --size "${SIZE:-source}" --resolution "${RESOLUTION:-1024}")
if [ -n "${IMAGES:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--image "$ONE"); done <<<"$IMAGES"
fi
[ -z "${WIDTH:-}" ] || [ -z "${HEIGHT:-}" ] || ARGS+=(--width "$WIDTH" --height "$HEIGHT")
[ -z "${MOST_PIXELS:-}" ] || ARGS+=(--most "$MOST_PIXELS")
[ "${BASE:-0}" != 1 ] || ARGS+=(--base --steps "${STEPS:-40}")
if [ -n "${SET_FILE:-}" ]; then ARGS+=(--set "@$SET_FILE"); else ARGS+=(--prompt "$PROMPT" -o "$OUT"); fi
if [ -n "${LORA:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--lora "$ONE"); done <<<"$LORA"
fi
PREFIX="$PREFIX" exec "$PREFIX/venv/bin/python" "$HERE/spark_edit.py" "${ARGS[@]}"
