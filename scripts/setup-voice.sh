#!/usr/bin/env bash
# Fish Audio Drama 3 for the Voice tab, optional (oneshot-setup.sh --voice and oneshot-setup-spark.sh --voice run this).
# Built with Fish Audio.
#
#   bash scripts/setup-voice.sh            # Mac: TensorFold's drama3-mlx in its own venv, Fish's checkpoint, the MLX conversion,
#                                          #      a test line. DGX Spark: the CUDA engine, Fish's checkpoint, the converted
#                                          #      codec, a test line (below)
#   bash scripts/setup-voice.sh --verify   # check only: no clone, no build, no install, no download
#   bash scripts/setup-voice.sh --upgrade  # DGX Spark: the engine moved to this version's pinned commit, no weights fetched
#   bash scripts/setup-voice.sh --no-test  # DGX Spark: no test line at the end
#
# Fish Audio Drama 3's weights and codec are Fish Audio Materials under the Fish Audio Community License v1.0:
# research, non-commercial and small-company commercial use; a hosted text-to-speech or voice-cloning service needs a
# licence of its own from Fish Audio; clone a real person's voice only with their consent. Nothing here calls Fish
# Audio's API; the weights come from Hugging Face (fishaudio/drama-3) and stay on this machine.
#
# Mac (Apple silicon, 96 GB or more): 39 GB for Fish's checkpoint, 39 GB for the MLX conversion, about 6 GB for the venv.
# Environment: DRAMA3_SRC (the TensorFold clone, default ~/tensorfold-drama3-mlx, or ~/TensorFold when that one has the
# port already; an existing clone is used as it is),
# DRAMA3_VENV (~/drama3-venv), DRAMA3_CHECKPOINT (~/models/drama-3), DRAMA3_MODEL (~/models/drama-3-mlx).
#
# DGX Spark (GB10), in $PREFIX/drama3 (~/.local/opt/tensorfold-studio/drama3), 39 GB for Fish's checkpoint and 1.6 GB
# for the codec:
#   1. the engine, TensorFold's native CUDA port (branch drama3-cuda at CUDA_COMMIT below): spark/build-drama3.sh takes
#      the carrier image's prebuilt drama3-generate and libtensorfold_drama3.so (checksums checked), else builds them
#      from source (nvcc + Zig, about two minutes);
#   2. Fish's checkpoint at ~/models/drama-3 (DRAMA3_CUDA_MODEL or DRAMA3_CHECKPOINT name another): a copy found elsewhere
#      on this machine is linked (app/discover.py), else it is downloaded from Hugging Face at a pinned revision
#      (DRAMA3_REVISION); if the repo is ever gated, the script says what to do (app/hf_gate.py);
#   3. the codec: Fish's codec.pth converted once into codec-v1.safetensors, on this machine, with the Studio's own
#      PyTorch ($PREFIX/venv) and Fish's reference codec code at pinned commits (spark/drama3_codec.py);
#   4. a test line, when 50 GB of memory is free and the GPU lock is free.
set -euo pipefail

REPO="https://github.com/drowzeys/TensorFold.git"
MLX_BRANCH="drama3-mlx"; MLX_COMMIT="8f3d4b33"
CUDA_BRANCH="drama3-cuda"; CUDA_COMMIT="81173e6c"  # spark/build-drama3.sh pins the whole hash
HF_REPO="fishaudio/drama-3"; CHECKPOINT_REVISION="b2b5ee998034b2975720906c815e7fb0e9f007bd"
# pinned floor for the floating pip installs below. every version here is one requirements.lock already installs into
# the Studio's own venv: copy, never invent. bump deliberately, the CI floor is this table. mlx-whisper and soundfile
# stay floating: no version of theirs is written down in this repo to copy.
PIP_PIN=(mlx==0.32.3 mlx-lm==0.32.0 torch==2.14.1 huggingface-hub==1.33.0)  # requirements.lock
VERIFY=0; UPGRADE=0; TEST=1
for arg in "$@"; do
  case "$arg" in
    --verify) VERIFY=1;;
    --upgrade) UPGRADE=1;;
    --no-test) TEST=0;;
    *) echo "unknown option $arg" >&2; exit 2;;
  esac
done
die() { echo "FATAL: $*" >&2; exit 1; }
ok()  { echo "  ✓ $*"; }
step(){ echo; echo "==> $*"; }
CHECKPOINT="${DRAMA3_CHECKPOINT:-$HOME/models/drama-3}"
# a folder the weights go into must be this user's to write: a root-owned ~/models stops here, before the engine is
# built, not after it with the checkpoint quietly missing
writable_for() {
  local dir="$1"
  while [ ! -e "$dir" ] && [ "$dir" != / ]; do dir="$(dirname "$dir")"; done
  [ -d "$dir" ] && [ -w "$dir" ] && return 0
  die "$dir is not writable by $(id -un) (it belongs to $(stat -c %U "$dir" 2>/dev/null || stat -f %Su "$dir")), so $1 cannot be made there. \
Make it yours (sudo chown $(id -un) \"$dir\") or point DRAMA3_CHECKPOINT (DRAMA3_MODEL on a Mac) at a folder of your own, then run this again."
}

licence() {
  echo "  Fish Audio Drama 3 (39 GB) is licensed under the Fish Audio Community License v1.0: research, non-commercial"
  echo "  and small-company commercial use; a hosted TTS or voice-cloning service needs Fish Audio's own licence."
  echo "  Read it: https://huggingface.co/fishaudio/drama-3"
}

if [ "$(uname -s)" = Linux ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
  BIN="${DRAMA3_CUDA_BIN:-$PREFIX/drama3/bin/drama3-generate}"
  LIB="${DRAMA3_CUDA_LIB:-$(dirname "$(dirname "$BIN")")/lib/libtensorfold_drama3.so}"
  CODEC="${DRAMA3_CUDA_CODEC:-$PREFIX/drama3/codec-v1.safetensors}"
  MODEL="${DRAMA3_CUDA_MODEL:-$CHECKPOINT}"
  REVISION="${DRAMA3_REVISION:-$CHECKPOINT_REVISION}"
  PY="$PREFIX/venv/bin/python"
  MISSING=0
  have_engine() { [ -x "$BIN" ] && [ -f "$LIB" ]; }
  # the checkpoint with every weight file its index names (an interrupted download is downloaded again: hf resumes)
  have_model() {
    python3 -I -c 'import json, os, sys; d = sys.argv[1]; m = json.load(open(os.path.join(d, "model.safetensors.index.json")))["weight_map"]
sys.exit(0 if os.path.isfile(os.path.join(d, "config.json")) and all(os.path.isfile(os.path.join(d, f)) for f in set(m.values())) else 1)' "$MODEL" 2>/dev/null
  }

  if [ "$VERIFY" = 0 ] && [ "$UPGRADE" = 0 ] && ! have_model; then writable_for "$MODEL"; fi
  step "TensorFold's Drama 3 engine for CUDA ($CUDA_BRANCH @ $CUDA_COMMIT) -> $(dirname "$(dirname "$BIN")")"
  if [ "$VERIFY" = 0 ] && [ -z "${DRAMA3_CUDA_BIN:-}" ]; then
    # spark/build-drama3.sh: the carrier image's prebuilt engine (checksums checked), else a build from source
    PREFIX="$PREFIX" bash "$HERE/spark/build-drama3.sh" || echo "  ! the engine did not build: fix what it says and run this again"
  fi
  if have_engine; then ok "drama3-generate and libtensorfold_drama3.so$([ ! -f "$PREFIX/drama3/commit" ] || echo " at $(cut -c1-8 "$PREFIX/drama3/commit")")"
  else echo "  ! no engine at $BIN (or no $LIB): bash spark/build-drama3.sh builds it"; MISSING=1; fi

  step "Fish's checkpoint: $MODEL"
  if ! have_model && [ "$VERIFY" = 0 ]; then
    # a copy elsewhere on this machine (a Hugging Face cache, another folder) is linked here instead of downloaded
    # (app/discover.py: a symlink, nothing moved or copied)
    said="$(PREFIX="$PREFIX" DRAMA3_CUDA_MODEL="$MODEL" DRAMA3_CUDA_CODEC="$CODEC" "$([ -x "$PY" ] && echo "$PY" || echo python3)" \
      "$HERE/app/discover.py" --scan --link --max-age 3600 --paths "$MODEL" 2>/dev/null)" || said=""
    [ -z "$said" ] || { echo "  found elsewhere on this machine, linked instead of downloading:"; echo "$said" | sed 's/^/    /'; }
  fi
  if have_model; then ok "there"
  elif [ "$VERIFY" = 1 ]; then echo "  ! no checkpoint at $MODEL"; MISSING=1
  elif [ "$UPGRADE" = 1 ]; then echo "  ! no checkpoint at $MODEL: --upgrade fetches no weights (bash oneshot-setup-spark.sh --voice)"; MISSING=1
  else
    licence
    # Fish's repo is open today; should it become gated, say what the person does (app/hf_gate.py, as --sound does)
    # (open: not gated; granted, sign_in, accept, pending: gated; unknown: the hub did not say, so the download tries)
    ACCESS="$("$PY" -I -c "import sys; sys.path.insert(0, sys.argv[1]); import hf_gate as g; r = g.gate_of(sys.argv[2])
print(r.get('access') if r.get('gated') in ('auto', 'manual') or r.get('access') == 'granted' else 'open' if r.get('gated') is False else 'unknown')" \
      "$HERE/app" "https://huggingface.co/$HF_REPO/resolve/$REVISION/config.json" 2>/dev/null || echo unknown)"
    have_engine || { echo "  (not downloaded while the engine is missing: it would not run here)"; ACCESS=skip; }
    mkdir -p "$(dirname "$MODEL")"
    FREE=$(df -BG --output=avail "$(dirname "$MODEL")" | tail -1 | tr -dc 0-9)
    case "$ACCESS" in
      open|granted|unknown)
        if [ "$FREE" -lt 42 ]; then echo "  ! $(dirname "$MODEL") has ${FREE} GB free; Fish's checkpoint needs 39 GB (and 1.6 GB for the codec)"
        else
          "$PREFIX/venv/bin/hf" download "$HF_REPO" --revision "$REVISION" --local-dir "$MODEL" >/dev/null \
            || echo "  ! the checkpoint did not download: run this again (hf download resumes)"
        fi;;
      sign_in) echo "  ! $HF_REPO is gated: sign in to Hugging Face (the studio's Install guide, or hf auth login), accept the licence on https://huggingface.co/$HF_REPO, then run this again";;
      accept)  echo "  ! $HF_REPO is gated: accept its licence on https://huggingface.co/$HF_REPO with your account, then run this again";;
      pending) echo "  ! $HF_REPO: your access request is waiting for Fish Audio's approval; run this again once it is granted";;
    esac
    if have_model; then ok "downloaded ($HF_REPO @ ${REVISION:0:8})"; else MISSING=1; fi
  fi

  step "The codec, converted for the CUDA engine: $CODEC"
  if [ -f "$CODEC" ]; then ok "there"
  elif [ "$VERIFY" = 1 ]; then echo "  ! no converted codec at $CODEC"; MISSING=1
  elif [ -f "$MODEL/codec.pth" ] && [ -x "$PY" ]; then
    # once, on this machine, from its own copy of Fish's codec.pth (no converted weights are published): TensorFold's
    # codec_convert.py with Fish's reference codec code, both at pinned commits, in the Studio's PyTorch environment
    echo "  converting the checkpoint's codec.pth (once, a few seconds; fetches the pinned reference code)"
    mkdir -p "$(dirname "$CODEC")"
    "$PY" -I -W ignore "$HERE/spark/drama3_codec.py" "$MODEL" "$CODEC" --work "$PREFIX/drama3/codec-src" | sed 's/^/  /' \
      || echo "  ! the codec was not converted: run this again"
    if [ -f "$CODEC" ]; then ok "converted"; else MISSING=1; fi
  else echo "  ! no converted codec at $CODEC: it is made from the checkpoint's codec.pth$([ -x "$PY" ] || echo " with $PY (oneshot-setup-spark.sh makes it)")"; MISSING=1; fi

  if [ "$MISSING" = 0 ] && [ "$VERIFY" = 0 ] && [ "$UPGRADE" = 0 ] && [ "$TEST" = 1 ]; then
    step "A test line"
    LOCK="${STUDIO_GPU_LOCK:-$HOME/studio-gpu.lock.d}"
    AVAIL=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
    if [ "$AVAIL" -lt 50 ]; then echo "  skipped: Drama 3 wants about 50 GB free and ${AVAIL} GB is available now (the Voice tab frees it itself)"
    elif ! mkdir "$LOCK" 2>/dev/null; then echo "  skipped: the GPU is in use ($LOCK)"
    else
      echo "setup-voice $$" > "$LOCK/owner"
      trap 'rm -rf "$LOCK"' EXIT
      WAV="$(mktemp -d)/drama3-test.wav"
      LD_LIBRARY_PATH="$(dirname "$LIB")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$BIN" --model "$MODEL" --codec "$CODEC" --seed 1 \
        --text "The rain had not stopped in three days, and neither had the phone. I let it ring." --wav "$WAV" >/dev/null 2>"$WAV.log" \
        || { tail -5 "$WAV.log" >&2; die "the test line failed (above)"; }
      rm -rf "$LOCK"; trap - EXIT
      "$PY" -I -c "$(cat <<'PYCHECK'
import sys, wave, array
with wave.open(sys.argv[1]) as w:
    rate, frames = w.getframerate(), w.readframes(w.getnframes())
pcm = array.array("h", frames)
peak = max((abs(v) for v in pcm), default=0)
print(f"{len(pcm) / rate:.1f} s at {rate} Hz, peak {peak / 32768:.2f}")
sys.exit(0 if len(pcm) > rate and peak > 300 else 1)
PYCHECK
)" "$WAV" | sed 's/^/  /' || die "the test line is silent or empty: $WAV"
      ok "$WAV"
    fi
  fi
  if [ "$MISSING" = 1 ]; then
    echo
    echo "  Not ready yet: run bash oneshot-setup-spark.sh --voice (or this script) again for what is missing above."
    [ "$VERIFY" = 1 ] && exit 1
    exit 0
  fi
  echo
  echo "  Voice tab ready: the app runs it as a persistent server, unloaded after five idle minutes. Built with Fish Audio."
  exit 0
fi

[ "$(uname -m)" = arm64 ] || die "the MLX port of Drama 3 needs Apple silicon"
MEM=$(sysctl -n hw.memsize)
# 96 GB: the Studio's minimum for a Mac (64 GB up to 4.3.0). --verify of an install made under the old floor says so and goes on
if [ "$MEM" -lt $((96 * 1024 * 1024 * 1024)) ]; then
  [ "$VERIFY" = 1 ] || die "Drama 3 (the Voice tab) peaks at about 52 GB: a Mac with 96 GB or more (this one has $((MEM / 1073741824)) GB)"
  echo "  ! this Mac has $((MEM / 1073741824)) GB: Drama 3 (the Voice tab) needs 96 GB or more now (64 GB up to 4.3.0); what is installed is checked and kept, and the app does not run it here"
fi
SRC="${DRAMA3_SRC:-$HOME/tensorfold-drama3-mlx}"
# a TensorFold clone at ~/TensorFold that has the port already (the README's earlier hand setup) is used as it is
if [ -z "${DRAMA3_SRC:-}" ] && [ ! -d "$SRC" ] && git -C "$HOME/TensorFold" merge-base --is-ancestor "$MLX_COMMIT" HEAD 2>/dev/null; then
  SRC="$HOME/TensorFold"
fi
VENV="${DRAMA3_VENV:-$HOME/drama3-venv}"
MODEL="${DRAMA3_MODEL:-$HOME/models/drama-3-mlx}"
PY="$VENV/bin/python"
if [ "$VERIFY" = 0 ] && ! { [ -f "$MODEL/config.json" ] && [ -f "$MODEL/codec.safetensors" ]; }; then
  [ -f "$CHECKPOINT/model.safetensors.index.json" ] || writable_for "$CHECKPOINT"
  writable_for "$MODEL"
fi

step "TensorFold $MLX_BRANCH @ $MLX_COMMIT in $SRC"
if [ -d "$SRC/.git" ]; then
  if git -C "$SRC" merge-base --is-ancestor "$MLX_COMMIT" HEAD 2>/dev/null; then
    ok "$SRC is at $(git -C "$SRC" rev-parse --short=8 HEAD) (has $MLX_COMMIT)"
  elif [ "$VERIFY" = 1 ]; then
    die "$SRC does not have $MLX_COMMIT (run without --verify)"
  elif [ -n "$(git -C "$SRC" status --porcelain)" ]; then
    die "$SRC has local changes and lacks $MLX_COMMIT: commit or move them, or set DRAMA3_SRC to another folder"
  else
    git -C "$SRC" fetch -q "$REPO" "$MLX_BRANCH" && git -C "$SRC" checkout -q --detach "$MLX_COMMIT" && ok "checked out $MLX_COMMIT"
  fi
else
  [ "$VERIFY" = 0 ] || die "no TensorFold clone at $SRC"
  git clone -q -b "$MLX_BRANCH" "$REPO" "$SRC" && git -C "$SRC" checkout -q --detach "$MLX_COMMIT" && ok "cloned"
fi

step "Its own environment: $VENV"
if [ ! -x "$PY" ]; then
  [ "$VERIFY" = 0 ] || die "no environment at $VENV"
  PY311="$(command -v python3.11 || echo /opt/homebrew/bin/python3.11)"
  [ -x "$PY311" ] || die "python3.11 is needed (brew install python@3.11)"
  "$PY311" -m venv "$VENV"
fi
if ! "$PY" -I -c "import tensorfold.drama3, tensorfold.families.drama3.stream, mlx_whisper" 2>/dev/null; then
  [ "$VERIFY" = 0 ] || die "the environment lacks tensorfold.drama3 (with streaming) or mlx-whisper"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e "$SRC" "${PIP_PIN[@]}" mlx-whisper soundfile
fi
"$PY" -I -c "import tensorfold.drama3, tensorfold.families.drama3.stream, mlx_whisper" || die "the environment does not import the engine"
ok "tensorfold.drama3 + mlx-whisper (the engine's own end-of-text check)"

step "Fish's checkpoint: $CHECKPOINT"
if [ -f "$CHECKPOINT/model.safetensors.index.json" ] && [ -f "$CHECKPOINT/codec.pth" ]; then
  ok "there"
elif [ -f "$MODEL/config.json" ] && [ -f "$MODEL/codec.safetensors" ]; then
  ok "not needed: the MLX conversion is there"
else
  [ "$VERIFY" = 0 ] || die "no checkpoint at $CHECKPOINT"
  licence
  "$VENV/bin/hf" download fishaudio/drama-3 --local-dir "$CHECKPOINT"
fi

step "The MLX conversion: $MODEL"
if [ -f "$MODEL/config.json" ] && [ -f "$MODEL/codec.safetensors" ]; then
  ok "there"
else
  [ "$VERIFY" = 0 ] || die "no converted model at $MODEL"
  "$PY" -I -m tensorfold.families.drama3.convert "$CHECKPOINT" "$MODEL"
  ok "converted (8-bit experts)"
fi

if [ "$VERIFY" = 0 ]; then
  step "A test line"
  OUT="$(mktemp -d)/drama3-test.wav"
  "$PY" -I -m tensorfold.drama3 --model "$MODEL" --text "Hello there. The Voice tab is ready." --out "$OUT" --seed 1 | tail -1
  [ -s "$OUT" ] || die "the test line wrote nothing"
  ok "$OUT"
fi
echo
echo "Voice tab ready (DRAMA3_VENV=$VENV, DRAMA3_MODEL=$MODEL if not the defaults). Built with Fish Audio."
