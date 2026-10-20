#!/bin/bash
# Render one clip with MiniMax H3 on TensorFold.
# Standard: the Turbo adapter makes the picture in 5 passes, then the model without the adapter makes the sound again
# against the finished picture (20 audio steps). QUALITY=high: 20 steps without the adapter, fast recipe. QUALITY=full: plain 20 steps.
#   bash scripts/video.sh "a prompt" out.mp4
#   PROMPT_FILE=prompts/black-mirror-scene.txt bash scripts/video.sh "" out.mp4
#   QUALITY=high bash scripts/video.sh "a prompt" out.mp4               # 20 steps, no adapter, fast recipe (QUALITY=full for plain)
#   FIRST_FRAME=photo.jpg bash scripts/video.sh "what happens next" out.mp4   # image to video
#   FIRST_FRAME=a.jpg LAST_FRAME=b.jpg bash scripts/video.sh "how a becomes b" out.mp4   # the clip ends on b.jpg
#   REF_IMAGES="face.jpg" bash scripts/video.sh "Use <Picture 1> as the person. The person ..." out.mp4   # reference to video
#   REF_VIDEOS="dance.mp4" bash scripts/video.sh "The robot dances with the motion of <Video 1>." out.mp4   # a reference video
#   REF_IMAGES="face.jpg" REF_AUDIOS="voice.wav" bash scripts/video.sh "<Picture 1> speaks with the voice of <Audio 1>: ..." out.mp4
#   X2=1 WIDTH=1344 HEIGHT=768 bash scripts/video.sh "a prompt" out.mp4   # generate at 672x384, decode at 2x
#   ENGINE=fasth3 STEPS=8 bash scripts/video.sh "a prompt" out.mp4       # FastH3; see scripts/fast.sh
# Environment: PREFIX, H3_MODEL_DIR, ADAPTER (a file, or none), FIRST_FRAME (an image the clip starts from; it is
# stretched onto the canvas, so match its aspect ratio), LAST_FRAME (an image the clip ends on: MiniMax H3's
# first-and-last-frame mode, with or without FIRST_FRAME; beside a first frame it is fitted by a centre crop), WIDTH, HEIGHT, FRAMES (17n+5), SEED,
# LORA (style LoRAs, one PATH:STRENGTH per line), QUALITY (standard, high or full), POINTS (sigma points, one more than the passes: default 6 with the adapter, 21
# without), REVOICE (audio steps made again without the adapter after an adapter render: default 20, 0 keeps the
# adapter's own sound), AUDIO_EQ / AUDIO_BASS / AUDIO_BASS_TARGET (see below), AUDIO_SHIFT (experimental: the audio schedule's shift,
# 3 in the model; lower values add bass but roughen speech), EXTRA (extra flags for h3_generate.py).
# ENGINE=fasth3 swaps the transformer for FastVideo's distilled FastH3 ($FASTH3_DIR, fetched by
# `oneshot-setup.sh`) with its sparse attention on TensorFold's tile kernel. STEPS is the number of passes:
# 8 is what the checkpoint was trained for, 4 is faster and softer, 20 slower with more texture. FIRST_FRAME works
# too, though FastVideo trained FastH3 on text to video only.
# FastH3's passes run on TensorFold 1.0's native Zig + Metal runtime ($PREFIX/zig-engine) when it is installed and
# the chip has tensor units (M5 or later), with or without FIRST_FRAME; a machine without the engine uses the MLX
# engine as before. FASTH3_ENGINE=zig or mlx forces one.
# REF_IMAGES (reference to video, MiniMax H3 Ref2VA: `oneshot-setup.sh --ref2va`) is one to nine pictures, separated
# by spaces (one per line when a path has spaces). They are not frames of the clip: each fixes how a person or a
# thing looks, and the prompt names them <Picture 1>, <Picture 2>, ... in that order. A picture is used at the clip's
# pixel area with its own aspect ratio, so a portrait can guide a landscape clip. Plain 20 passes by default (3 min
# 43 s for 5 s at 864x480 on an M5 Ultra). REF_TURBO=1 uses LightX2V's 4-step Ref2VA adapter instead ($REF_ADAPTER,
# installed by --ref2va): 4 passes, then the sound made again by the Ref2VA model without the adapter (REVOICE=20),
# 1 min 28 s for the same clip. QUALITY=high is the 20 passes with the fast recipe (1 min 58 s). Not with FIRST_FRAME,
# and not with ENGINE=fasth3.
# STEPS=N sets the number of passes of a reference clip by hand (POINTS=N+1 does the same and wins when both are
# set), with or without REF_TURBO=1: the adapter then runs N passes instead of the 4 it was trained for, and the
# render says so. The first line of a reference render names the passes it will run.
# REF_VIDEOS (one to three clips, each 2 to 15 s, 15 s in all) and REF_AUDIOS (one to three clips, each 2 to 15 s, 15 s
# in all, and only beside a picture or a video) are the same model's other references, on the Mac only: <Video 1>, ...
# carry a subject and how it moves, <Audio 1>, ... a voice or music. At most 12 references of all kinds. The model
# reads pictures first, then videos, then audio clips. A video is read at 24 fps up to the clip's own length and
# brought down to the clip's pixel area (REF_VIDEO_SIZE=full keeps the model's 768-pixel canvas, several times
# slower); its own sound is ignored unless REF_VIDEO_SOUND=1, which makes each video's soundtrack a reference too,
# numbered <Audio 1>, ... ahead of the REF_AUDIOS clips. Audio is cut to the clip's length. Every reference latent
# rides through every pass: a 5 s reference video doubles the rows of a 5 s clip. Needs ffmpeg on the PATH.
# TEXT_ENCODER may be the path of another MiniMax H3 text encoder file (a ComfyUI int8 safetensors, decoded to
# bfloat16 as it loads); unset or regular is MiniMax's own. With ENGINE=fasth3 it renders on the MLX engine.
set -euo pipefail
# On a DGX Spark (Linux, CUDA) the same interface is served by spark/video.sh.
[ "$(uname -s)" != Linux ] || exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/spark/video.sh" "$@"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
H3_MODEL_DIR="${H3_MODEL_DIR:-$HOME/h3-models/MiniMax-H3}"
# QUALITY=high: 20 steps without the adapter, with the fast recipe (the first four and last two steps in full, a
# velocity cache and attention reuse in between: about twice as fast as plain). QUALITY=full: plain 20 steps.
case "${QUALITY:-standard}" in high) ADAPTER=none; RECIPE="--step-cache 0.05 --attention-every 2";; full) ADAPTER=none; RECIPE="";; *) RECIPE="";; esac
FASTH3_DIR="${FASTH3_DIR:-$HOME/h3-models/FastH3-8-Step-V2}"
# TEXT_ENCODER: regular (MiniMax's own, in the model folder) or a file's path
case "${TEXT_ENCODER:-regular}" in
  regular) TEXT_ENCODER="";;
  *) [ -f "$TEXT_ENCODER" ] || { echo "TEXT_ENCODER is regular or a safetensors file, and there is no file at $TEXT_ENCODER" >&2; exit 2; };;
esac
# MiniMax H3's own transformer (the Turbo adapter and the 20-step qualities run on it) is an optional part of the
# install: oneshot-setup.sh --turbo. Without it the engine is FastH3, which needs only H3's text encoder and decoders.
TURBO_OK=0; ls "$H3_MODEL_DIR"/FL2VA/transformer/*.safetensors >/dev/null 2>&1 && TURBO_OK=1
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
  # reference to video runs on the Ref2VA transformer; the text encoder and the two decoders are FL2VA's own files
  [ -z "${FIRST_FRAME:-}${LAST_FRAME:-}" ] || { echo "references (REF_IMAGES, REF_VIDEOS, REF_AUDIOS) and FIRST_FRAME / LAST_FRAME do not combine: a reference is not a frame of the clip" >&2; exit 2; }
  [ "${ENGINE:-h3}" = h3 ] || { echo "references need MiniMax H3's Ref2VA transformer, not ENGINE=$ENGINE" >&2; exit 2; }
  # MiniMax's limits for one request: 9 pictures, 3 videos, 3 audio clips, 12 files in all
  [ "${#REFS[@]}" -le 9 ] || { echo "at most 9 reference pictures, got ${#REFS[@]}" >&2; exit 2; }
  [ "${#REF_CLIPS[@]}" -le 3 ] || { echo "at most 3 reference videos, got ${#REF_CLIPS[@]}" >&2; exit 2; }
  [ "${#REF_SOUNDS[@]}" -le 3 ] || { echo "at most 3 reference audio clips, got ${#REF_SOUNDS[@]}" >&2; exit 2; }
  [ "$REF_COUNT" -le 12 ] || { echo "at most 12 references in all, got $REF_COUNT" >&2; exit 2; }
  [ "${#REF_SOUNDS[@]}" = 0 ] || [ $(( ${#REFS[@]} + ${#REF_CLIPS[@]} )) -gt 0 ] \
    || { echo "REF_AUDIOS needs a reference picture or video beside it (REF_IMAGES or REF_VIDEOS): the model does not take sound alone" >&2; exit 2; }
  for REF in ${REFS[@]+"${REFS[@]}"}; do [ -s "$REF" ] || { echo "no reference picture at $REF" >&2; exit 2; }; done
  for REF in ${REF_CLIPS[@]+"${REF_CLIPS[@]}"}; do [ -s "$REF" ] || { echo "no reference video at $REF" >&2; exit 2; }; done
  for REF in ${REF_SOUNDS[@]+"${REF_SOUNDS[@]}"}; do [ -s "$REF" ] || { echo "no reference audio clip at $REF" >&2; exit 2; }; done
  case "${REF_VIDEO_SIZE:-match}" in match|full) ;; *) echo "REF_VIDEO_SIZE is match or full, got $REF_VIDEO_SIZE" >&2; exit 2;; esac
  # each video and each audio clip is 2 to 15 seconds, and 15 seconds in all per kind: the renderer measures them
  # and says which file is out of range before any model is loaded
  ls "$H3_MODEL_DIR"/Ref2VA/transformer/*.safetensors >/dev/null 2>&1 \
    || { echo "MiniMax H3's Ref2VA transformer is not installed (oneshot-setup.sh --ref2va adds it: 62 GB, 128 GB of memory)" >&2; exit 2; }
  for PART in text_encoder tokenizer processor video_vae audio_vae; do
    [ -e "$H3_MODEL_DIR/Ref2VA/$PART" ] || [ ! -e "$H3_MODEL_DIR/FL2VA/$PART" ] || ln -s "../FL2VA/$PART" "$H3_MODEL_DIR/Ref2VA/$PART"
  done
  ENGINE=h3; TURBO_OK=1
  # a number of passes set by hand: STEPS, as for FastH3 and on a Spark (POINTS, one more, still works and wins)
  if [ -n "${STEPS:-}" ] && [ -z "${POINTS:-}" ]; then
    case "$STEPS" in *[!0-9]*|0) echo "STEPS must be a positive number of passes, got $STEPS" >&2; exit 2;; esac
    POINTS=$((STEPS + 1))
  fi
  # the FL2VA Turbo adapter is not for this transformer: plain 20 passes, or the Ref2VA adapter with REF_TURBO=1
  ADAPTER=none
  if [ "${REF_TURBO:-0}" = 1 ]; then
    ADAPTER="${REF_ADAPTER:-$PREFIX/adapters/lightx2v_ref2v_4step_v0.1_ourlayout.safetensors}"; RECIPE=""
    [ -f "$ADAPTER" ] || { echo "no Ref2VA Turbo adapter at $ADAPTER (oneshot-setup.sh --ref2va adds it)" >&2; exit 2; }
    POINTS="${POINTS:-5}"  # four passes, what the adapter was trained for
    [ "$POINTS" = 5 ] || echo "[tensorfold] the Ref2VA Turbo adapter was trained for 4 passes; running $((POINTS - 1)) as asked"
  fi
  case "${POINTS:-21}" in *[!0-9]*|0|1) echo "POINTS is one more than the passes, 2 at least, got ${POINTS}" >&2; exit 2;; esac
  echo "[tensorfold] reference to video: $(( ${POINTS:-21} - 1 )) passes$([ "$ADAPTER" = none ] || echo " with the Ref2VA Turbo adapter"), ${#REFS[@]} picture(s), ${#REF_CLIPS[@]} video(s), ${#REF_SOUNDS[@]} audio clip(s)"
fi
ENGINE="${ENGINE:-$([ "$TURBO_OK" = 1 ] && echo h3 || echo fasth3)}"; STEPS="${STEPS:-8}"
[ "$ENGINE" = fasth3 ] || [ "$TURBO_OK" = 1 ] || { echo "MiniMax H3's transformer is not installed (oneshot-setup.sh --turbo adds it, 128 GB of memory): use ENGINE=fasth3" >&2; exit 2; }
if [ "$ENGINE" = fasth3 ]; then
  ADAPTER=none; RECIPE=""
  [ -f "$FASTH3_DIR/fastvideo_inference.json" ] || { echo "no FastH3 checkpoint at $FASTH3_DIR (run oneshot-setup.sh, or set FASTH3_DIR)" >&2; exit 2; }
  case "$STEPS" in ''|*[!0-9]*|0) echo "STEPS must be a positive number of passes, got $STEPS" >&2; exit 2;; esac
fi
ADAPTER="${ADAPTER:-$PREFIX/adapters/lightx2v_v1.0_768p_ourlayout.safetensors}"
# sigma points are one more than the passes: 5 passes with the adapter, 20 without
if [ "$ADAPTER" = none ]; then POINTS="${POINTS:-21}"; REVOICE="${REVOICE:-0}"; else POINTS="${POINTS:-6}"; REVOICE="${REVOICE:-20}"; fi
X2_VAE="${X2_VAE:-$PREFIX/adapters/MiniMax-H3-X2-Detail-v1.safetensors}"
PROMPT="${1:-}"; OUT="${2:-outputs/h3.mp4}"
mkdir -p "$(dirname "$OUT")"
export PATH="/opt/homebrew/bin:$PATH" PYTHONPATH="$PREFIX/minimax-h3-mlx"
if [ "${X2:-0}" = 1 ]; then
  W="${WIDTH:-1344}"; H="${HEIGHT:-768}"
  [ $((W % 64)) = 0 ] && [ $((H % 64)) = 0 ] || { echo "with X2=1, WIDTH and HEIGHT must be multiples of 64, got ${W}x${H}" >&2; exit 2; }
  [ -f "$X2_VAE" ] || { echo "no 2x decoder at $X2_VAE (run oneshot-setup.sh, or set X2_VAE)" >&2; exit 2; }
  W=$((W / 2)); H=$((H / 2))
else
  W="${WIDTH:-864}"; H="${HEIGHT:-480}"
fi
ARGS=("$H3_MODEL_DIR" -o "$OUT" --width "$W" --height "$H" --frames "${FRAMES:-124}"
      --seed "${SEED:-0}" --points "$POINTS")
# The int8 tensor-unit kernels run the block MLPs and the two attention projections. One exception, by measurement:
# the Ref2VA Turbo adapter with a reference video. With all three kernels the clip lost the video's fast motion (pose
# distance 26 % of the frame width against 3.7 % in bfloat16; any two kernels 14 to 23 %, any one alone 3.4 to 3.5 %),
# while 20 passes without the adapter held with them (4.2 %). So that one case runs in bfloat16: about 13 s more a
# pass at 864x480. REF_INT8=1 puts the kernels back.
if [ "$ADAPTER" != none ] && [ "${#REF_CLIPS[@]}" -gt 0 ] && [ "${REF_INT8:-0}" != 1 ]; then
  echo "[tensorfold] reference video with the Turbo adapter: passes in bfloat16, without the int8 kernels"
elif [ "${INT8:-1}" = 0 ]; then
  echo "[tensorfold] INT8=0: passes in bfloat16, without the int8 kernels"
else
  ARGS+=(--int8-mlp --int8-qkv --int8-out)
fi
[ "$ENGINE" != fasth3 ] || ARGS+=(--fasth3 "$FASTH3_DIR" --fasth3-steps "$STEPS")
[ "$REVOICE" = 0 ] || ARGS+=(--revoice "$REVOICE")
[ "${X2:-0}" != 1 ] || ARGS+=(--upscale-vae "$X2_VAE")
if [ "$ENGINE" = fasth3 ]; then
  # which engine runs the passes, and why
  ZIG_ENGINE="${ZIG_ENGINE:-$PREFIX/zig-engine}"; WHY=""
  [ -x "$ZIG_ENGINE/tf-h3-dit" ] && [ -f "$PREFIX/zig_fasth3.py" ] || WHY="the native engine is not installed"
  [ -n "$WHY" ] || sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -Eq 'Apple M([5-9]|[1-9][0-9])' || WHY="this chip has no tensor units"
  [ -n "$WHY" ] || [ -z "${AUDIO_SHIFT:-}${EXTRA:-}" ] || WHY="AUDIO_SHIFT and EXTRA are MLX options"
  [ -n "$WHY" ] || [ -z "$TEXT_ENCODER" ] || WHY="TEXT_ENCODER is an MLX option"
  # a LoRA is merged into the native engine's weights (bf16 + update in float32, then its int8 rounding) when the
  # engine can; an older engine, or an adapter that changes more than the block projections, renders on MLX
  [ -n "$WHY" ] || [ -z "${LORA:-}" ] || grep -q -- '"--lora"' "$ZIG_ENGINE/h3_case.py" \
    || WHY="a LoRA is merged by the MLX engine (this native engine is older)"
  [ -n "$WHY" ] || [ -z "${LAST_FRAME:-}" ] || WHY="a last frame is held by the MLX engine"
  case "${FASTH3_ENGINE:-auto}" in
    mlx) WHY="FASTH3_ENGINE=mlx";;
    zig) [ -z "$WHY" ] || { echo "FASTH3_ENGINE=zig, but $WHY" >&2; exit 2; };;
    auto) ;;
    *) echo "FASTH3_ENGINE is zig or mlx, got $FASTH3_ENGINE" >&2; exit 2;;
  esac
  if [ -z "$WHY" ]; then
    ZARGS=("$H3_MODEL_DIR" --fasth3 "$FASTH3_DIR" --engine "$ZIG_ENGINE" -o "$OUT" --width "$W" --height "$H"
           --frames "${FRAMES:-124}" --seed "${SEED:-0}" --steps "$STEPS")
    [ "${X2:-0}" != 1 ] || ZARGS+=(--upscale-vae "$X2_VAE")
    [ -z "${CROP:-}" ] || ZARGS+=(--crop "$CROP")
    [ -z "${FIRST_FRAME:-}" ] || ZARGS+=(--first-frame "$FIRST_FRAME")
    if [ -n "${PROMPT_FILE:-}" ]; then ZARGS+=(--prompt-file "$PROMPT_FILE"); else ZARGS+=(--prompt "$PROMPT"); fi
    [ -n "${LORA:-}" ] || exec "$PREFIX/venv/bin/python" "$PREFIX/zig_fasth3.py" "${ZARGS[@]}"
    while IFS= read -r ONE; do [ -z "$ONE" ] || ZARGS+=(--lora "$ONE"); done <<<"$LORA"
    # status 3: the native engine cannot apply this adapter (it was refused before any pass); the MLX engine can
    STATUS=0; "$PREFIX/venv/bin/python" "$PREFIX/zig_fasth3.py" "${ZARGS[@]}" || STATUS=$?
    [ "$STATUS" = 3 ] || exit "$STATUS"
    [ "${FASTH3_ENGINE:-auto}" != zig ] || { echo "FASTH3_ENGINE=zig, but the native engine cannot apply this LoRA" >&2; exit 2; }
    WHY="the native engine cannot apply this LoRA"
  fi
  # On a chip without tensor units the MLX engine cannot use the int8 kernels and would hold the whole checkpoint
  # in bfloat16 (64 GiB). With the 8-bit weights written by fasth3_q8.py it holds half of that (FASTH3_Q8=0 or 1
  # forces the choice). FastVideo's SIMD-group routing is the faster of its two forms there.
  Q8="${FASTH3_Q8:-auto}"
  if [ "$Q8" = auto ]; then Q8=0; [ -f "$FASTH3_DIR/transformer-q8/q8.json" ] && [ "$WHY" = "this chip has no tensor units" ] && Q8=1; fi
  if [ "$Q8" = 1 ]; then
    [ -f "$FASTH3_DIR/transformer-q8/q8.json" ] || { echo "no 8-bit weights at $FASTH3_DIR/transformer-q8 (run oneshot-setup.sh, or $PREFIX/venv/bin/python $PREFIX/fasth3_q8.py convert $FASTH3_DIR)" >&2; exit 2; }
    ARGS+=(--fasth3-q8 --vsa-impl simd); WHY="$WHY, 8-bit weights"
  fi
  echo "[tensorfold] engine: mlx ($WHY)"
fi
[ "$ADAPTER" = none ] || ARGS+=(--lora "$ADAPTER")
# style LoRAs from the hub, one PATH:STRENGTH per line, already in the layout --lora reads (scripts/convert-lora.py)
if [ -n "${LORA:-}" ]; then while IFS= read -r ONE; do [ -z "$ONE" ] || ARGS+=(--lora "$ONE"); done <<<"$LORA"; fi
[ -z "${AUDIO_SHIFT:-}" ] || ARGS+=(--audio-shift "$AUDIO_SHIFT")
[ -z "${FIRST_FRAME:-}" ] || ARGS+=(--first-frame "$FIRST_FRAME")
[ -z "${LAST_FRAME:-}" ] || { [ -s "$LAST_FRAME" ] || { echo "no last frame at $LAST_FRAME" >&2; exit 2; }; ARGS+=(--last-frame "$LAST_FRAME"); }
[ -z "$TEXT_ENCODER" ] || ARGS+=(--text-encoder "$TEXT_ENCODER")
for REF in ${REFS[@]+"${REFS[@]}"}; do ARGS+=(--ref-image "$REF"); done
for REF in ${REF_CLIPS[@]+"${REF_CLIPS[@]}"}; do ARGS+=(--ref-video "$REF"); done
for REF in ${REF_SOUNDS[@]+"${REF_SOUNDS[@]}"}; do ARGS+=(--ref-audio "$REF"); done
[ "${#REF_CLIPS[@]}" = 0 ] || [ "${REF_VIDEO_SOUND:-0}" != 1 ] || ARGS+=(--ref-video-sound)
[ "${#REF_CLIPS[@]}" = 0 ] || [ "${REF_VIDEO_SIZE:-match}" = match ] || ARGS+=(--ref-video-size "$REF_VIDEO_SIZE")
if [ -n "${PROMPT_FILE:-}" ]; then ARGS+=(--prompt-file "$PROMPT_FILE"); else ARGS+=(--prompt "$PROMPT"); fi
[ -z "${CROP:-}" ] || ARGS+=(--crop "$CROP")
# shellcheck disable=SC2086
"$PREFIX/venv/bin/python" "${H3_GENERATE:-$PREFIX/h3_generate.py}" "${ARGS[@]}" $RECIPE ${EXTRA:-}
# Few-step audio varies a lot from take to take, and some takes come out thin (little 120-300 Hz). With an adapter,
# the uncompressed track the render leaves beside the clip is measured: when the bass band is under the level of a
# full take, a low shelf lifts it by what is missing (6 dB at most) and a limiter guards the peaks. A take that is
# already full is left exactly as rendered. The picture is never re-encoded. AUDIO_EQ=off skips this;
# AUDIO_BASS=<dB> forces a gain; AUDIO_BASS_TARGET sets the bass to mid-band energy ratio aimed for (default 0.5).
WAV="${OUT%.*}.wav"
if [ "$ADAPTER" != none ] && [ "${AUDIO_EQ:-on}" != off ] && [ -s "$WAV" ]; then
  GAIN="${AUDIO_BASS:-$("$PREFIX/venv/bin/python" - "$WAV" "${AUDIO_BASS_TARGET:-0.5}" <<'PY'
import sys, wave
import numpy as np
with wave.open(sys.argv[1]) as handle:
    rate = handle.getframerate()
    audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float32)
    audio = audio.reshape(-1, handle.getnchannels()).mean(axis=1)
power = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
freq = np.fft.rfftfreq(len(audio), 1 / rate)
bass = power[(freq >= 120) & (freq < 300)].sum()
mid = power[(freq >= 300) & (freq < 4000)].sum()
ratio = bass / max(mid, 1e-20)
gain = float(np.clip(10 * np.log10(float(sys.argv[2]) / max(ratio, 1e-6)), 0.0, 6.0))
print(f"{gain:.1f}")
PY
)}"
  if [ "$(printf '%.0f' "$(echo "$GAIN * 10" | bc)")" -ge 10 ]; then
    FILTER="bass=g=$GAIN:f=250:w=0.6,alimiter=limit=0.95:level=disabled"
    ffmpeg -v error -y -i "$OUT" -i "$WAV" -map 0:v -map 1:a -c:v copy -af "$FILTER" -c:a aac -b:a 256k "${OUT%.*}.eq.mp4"
    mv "${OUT%.*}.eq.mp4" "$OUT"
    echo "[tensorfold] thin take: bass lifted by $GAIN dB"
  else
    echo "[tensorfold] audio left as rendered (bass is full)"
  fi
fi
