#!/bin/bash
# One clip with FastH3 on a DGX Spark (CUDA). scripts/video.sh hands over to this on Linux; same interface:
#   bash scripts/video.sh "a prompt" out.mp4
#   FIRST_FRAME=photo.jpg bash scripts/video.sh "what happens next" out.mp4
#   FIRST_FRAME=a.jpg LAST_FRAME=b.jpg bash scripts/video.sh "how a becomes b" out.mp4   # the clip ends on b.jpg
#   WIDTH=1344 HEIGHT=768 STEPS=8 FRAMES=124 SEED=3 bash scripts/video.sh "a prompt" out.mp4
#   REF_IMAGES="face.png" bash scripts/video.sh "Use <Picture 1> as the person. She walks into a cafe." out.mp4
#   REF_IMAGES="face.png" REF_VIDEOS="move.mp4" bash scripts/video.sh "<Picture 1> moves as in <Video 1>." out.mp4
# REF_IMAGES (one to nine pictures, separated by spaces; one per line when a path has spaces) renders with MiniMax H3
# Ref2VA (`oneshot-setup-spark.sh --ref2va`), which holds a face or a subject through the clip: 20 steps by default,
# REF_TURBO=1 for LightX2V's 4-pass Ref2VA adapter (107 s against 321 s for 5 s at 864x480). REF_SIZE=match|max.
# STEPS=N sets the passes of a reference clip by hand, with or without REF_TURBO=1.
# REF_VIDEOS (one to three clips, each 2 to 15 s, 15 s in all: <Video 1>, ...) and REF_AUDIOS (one to three sound clips,
# same lengths, only beside a picture or a video: <Audio 1>, ...) go to the same model through ComfyUI's node. A video
# is read at 24 fps up to the clip's own length and brought down to the clip's pixel area (always: REF_VIDEO_SIZE=full,
# the model's 768-pixel canvas, is several times the rows and the memory and is refused here); REF_VIDEO_SOUND=1 makes each video's own sound a
# reference too, numbered ahead of the REF_AUDIOS clips. Every second of a reference video adds rows to every pass.
# Environment: PREFIX, WIDTH, HEIGHT (multiples of 32, default 864x480), FRAMES (17n+5), SEED, STEPS (4, 8 or 20
# passes), FIRST_FRAME, LAST_FRAME (the picture the clip ends on), SPARK_BASE=h3 (MiniMax H3's own transformer in
# place of FastH3, when oneshot-setup-spark.sh --h3-base has fetched it: 25 passes by default), PROMPT_FILE, CROP=WxH, SPARK_WEIGHTS (bf16, the default, or int8: no faster on a Spark, 22 GB
# less memory), SPARK_SPARSITY (0 is dense attention; unset picks by size), SPARK_ENGINE (tensorfold: the transformer's
# blocks on TensorFold's CUDA engine in int8, the default once it is built; comfy: ComfyUI's own blocks in bf16).
# TEXT_ENCODER names another MiniMax H3 text encoder file in ComfyUI's text_encoders folder; unset or regular is MiniMax's own.
# The clip's sound is made from a lossless copy of the decoder's output: 256 kb/s, and limited to -1 dBFS when the
# decoded peak is higher (the render says which). AUDIO_EQ=off keeps ComfyUI's own track.
# DELIVERY_AUDIO_RATE=48000 delivers the sound as stereo AAC at that rate (ComfyUI's H3 decoder makes 32 kHz): a
# resampling for an editor's timeline, the picture copied unchanged, not added detail. Unset, the clip keeps 32 kHz.
# Not on the Spark: MiniMax H3 Turbo (ENGINE=h3, QUALITY) and the 2x decoder (X2, TWOK, QHD).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/gpu.sh"
studio_pin_gpu
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
[ "${ENGINE:-fasth3}" = fasth3 ] || { echo "the Spark build renders with FastH3 only (ENGINE=fasth3)" >&2; exit 2; }
[ "${X2:-0}" != 1 ] || { echo "the 2x decoder is not part of the Spark build: use WIDTH and HEIGHT up to 1344x768" >&2; exit 2; }
[ -x "$PREFIX/venv/bin/python" ] || { echo "run oneshot-setup-spark.sh first: no environment at $PREFIX" >&2; exit 1; }
PROMPT="${1:-}"; OUT="${2:-outputs/h3.mp4}"
ARGS=(video -o "$OUT" --width "${WIDTH:-864}" --height "${HEIGHT:-480}" --frames "${FRAMES:-124}"
      --seed "${SEED:-0}")
# a list of files from the environment: separated by spaces, or one per line when a path has spaces
ref_list() {
  LIST=()
  [ -n "$1" ] || return 0
  case "$1" in *$'\n'*) while IFS= read -r REF; do [ -z "$REF" ] || LIST+=("$REF"); done <<<"$1";;
    *) read -r -a LIST <<<"$1";; esac
}
ref_list "${REF_IMAGES:-}"; REFS=(${LIST[@]+"${LIST[@]}"})
ref_list "${REF_VIDEOS:-}"; REF_CLIPS=(${LIST[@]+"${LIST[@]}"})
ref_list "${REF_AUDIOS:-}"; REF_SOUNDS=(${LIST[@]+"${LIST[@]}"})
REF_COUNT=$(( ${#REFS[@]} + ${#REF_CLIPS[@]} + ${#REF_SOUNDS[@]} ))
if [ "$REF_COUNT" -gt 0 ]; then
  [ -z "${FIRST_FRAME:-}${LAST_FRAME:-}" ] || { echo "references (REF_IMAGES, REF_VIDEOS, REF_AUDIOS) and FIRST_FRAME / LAST_FRAME do not combine: a reference is not a frame of the clip" >&2; exit 2; }
  # MiniMax's limits for one request: 9 pictures, 3 videos, 3 audio clips, 12 files in all
  [ "${#REFS[@]}" -le 9 ] || { echo "at most 9 reference pictures, got ${#REFS[@]}" >&2; exit 2; }
  [ "${#REF_CLIPS[@]}" -le 3 ] || { echo "at most 3 reference videos, got ${#REF_CLIPS[@]}" >&2; exit 2; }
  [ "${#REF_SOUNDS[@]}" -le 3 ] || { echo "at most 3 reference audio clips, got ${#REF_SOUNDS[@]}" >&2; exit 2; }
  [ "$REF_COUNT" -le 12 ] || { echo "at most 12 references in all, got $REF_COUNT" >&2; exit 2; }
  [ "${#REF_SOUNDS[@]}" = 0 ] || [ $(( ${#REFS[@]} + ${#REF_CLIPS[@]} )) -gt 0 ] \
    || { echo "REF_AUDIOS needs a reference picture or video beside it (REF_IMAGES or REF_VIDEOS): the model does not take sound alone" >&2; exit 2; }
  for REF in ${REFS[@]+"${REFS[@]}"}; do [ -s "$REF" ] || { echo "no reference picture at $REF" >&2; exit 2; }; ARGS+=(--ref "$REF"); done
  for REF in ${REF_CLIPS[@]+"${REF_CLIPS[@]}"}; do [ -s "$REF" ] || { echo "no reference video at $REF" >&2; exit 2; }; ARGS+=(--ref-video "$REF"); done
  for REF in ${REF_SOUNDS[@]+"${REF_SOUNDS[@]}"}; do [ -s "$REF" ] || { echo "no reference audio clip at $REF" >&2; exit 2; }; ARGS+=(--ref-audio "$REF"); done
  [ "${REF_VIDEO_SIZE:-match}" = match ] || { echo "REF_VIDEO_SIZE=$REF_VIDEO_SIZE is not offered on a DGX Spark: a reference video is always brought down to the clip's pixel area there (match), for memory" >&2; exit 2; }
  [ "${#REF_CLIPS[@]}" = 0 ] || [ "${REF_VIDEO_SOUND:-0}" != 1 ] || ARGS+=(--ref-video-sound)
  if [ -n "${STEPS:-}" ]; then
    case "$STEPS" in *[!0-9]*|0) echo "STEPS must be a positive number of passes, got $STEPS" >&2; exit 2;; esac
    ARGS+=(--steps "$STEPS")
  fi
  if [ "${REF_TURBO:-0}" = 1 ]; then ARGS+=(--ref-mode turbo); PASSES="${STEPS:-4}"; else ARGS+=(--ref-mode full); PASSES="${STEPS:-20}"; fi
  [ -z "${REF_SIZE:-}" ] || ARGS+=(--ref-size "$REF_SIZE")
  echo "[tensorfold] reference to video: $PASSES passes$([ "${REF_TURBO:-0}" != 1 ] || echo " with the Ref2VA Turbo adapter"), ${#REFS[@]} picture(s), ${#REF_CLIPS[@]} video(s), ${#REF_SOUNDS[@]} audio clip(s)"
  KIND="MiniMax H3 Ref2VA, ${#REFS[@]} picture(s), ${#REF_CLIPS[@]} video(s), ${#REF_SOUNDS[@]} audio clip(s), $PASSES passes"
else
  if [ "${SPARK_BASE:-fasth3}" = h3 ]; then ARGS+=(--base h3 --steps "${STEPS:-25}"); KIND="MiniMax H3 ${STEPS:-25} passes"
  else ARGS+=(--steps "${STEPS:-8}"); KIND="FastH3 ${STEPS:-8} passes"; fi
fi
[ -z "${FIRST_FRAME:-}" ] || ARGS+=(--first-frame "$FIRST_FRAME")
[ -z "${LAST_FRAME:-}" ] || { [ -s "$LAST_FRAME" ] || { echo "no last frame at $LAST_FRAME" >&2; exit 2; }; ARGS+=(--last-frame "$LAST_FRAME"); }
[ -z "${TEXT_ENCODER:-}" ] || ARGS+=(--text-encoder "$TEXT_ENCODER")
[ -z "${CROP:-}" ] || ARGS+=(--crop "$CROP")
[ -z "${SPARK_SPARSITY:-}" ] || ARGS+=(--sparsity "$SPARK_SPARSITY")
[ -z "${SPARK_ENGINE:-}" ] || ARGS+=(--engine "$SPARK_ENGINE")
# LoRAs from the hub, one PATH:STRENGTH per line. ComfyUI merges them into the bf16 weights: FastH3's for a clip
# from text or a first frame, MiniMax H3 Ref2VA's for a clip from references (with or without REF_TURBO=1).
if [ -n "${LORA:-}" ]; then
  while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--lora "$ONE"); done <<<"$LORA"
fi
# REF_PLAIN=1: a reference clip's prompt goes to the model as written (a LoRA trained on plain captions)
[ "$REF_COUNT" = 0 ] || [ "${REF_PLAIN:-0}" != 1 ] || ARGS+=(--ref-plain)
if [ -n "${PROMPT_FILE:-}" ]; then ARGS+=(--prompt-file "$PROMPT_FILE"); else ARGS+=(--prompt "$PROMPT"); fi
START=$(date +%s)
PREFIX="$PREFIX" "$PREFIX/venv/bin/python" "$HERE/spark_generate.py" "${ARGS[@]}"
if [ -n "${DELIVERY_AUDIO_RATE:-}" ] && command -v ffprobe >/dev/null; then
  [[ "$DELIVERY_AUDIO_RATE" =~ ^(44100|48000|96000)$ ]] || { echo "DELIVERY_AUDIO_RATE is 44100, 48000 or 96000" >&2; exit 2; }
  AUDIO_FORMAT="$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels -of csv=p=0 "$OUT")"
  if [ -n "$AUDIO_FORMAT" ] && [ "$AUDIO_FORMAT" != "$DELIVERY_AUDIO_RATE,2" ]; then
    NORMALIZED="$(mktemp "${OUT%.*}.audio-XXXXXX.mp4")"
    trap 'rm -f "$NORMALIZED"' EXIT
    ffmpeg -v error -y -i "$OUT" -map 0:v:0 -map 0:a:0 -c:v copy -c:a aac -b:a 256k -ar "$DELIVERY_AUDIO_RATE" -ac 2 -movflags +faststart "$NORMALIZED"
    mv "$NORMALIZED" "$OUT"
    trap - EXIT
    echo "[studio] delivery sound: stereo $((DELIVERY_AUDIO_RATE / 1000)) kHz AAC (the picture copied unchanged)"
  fi
fi
echo "[studio] $KIND, ${WIDTH:-864}x${HEIGHT:-480}, total $(( $(date +%s) - START )) s -> $OUT"
