#!/usr/bin/env bash
# MiniMax Music 3 for the Music tab, optional (oneshot-setup.sh --music runs this on a Mac). MiniMax-Music3.
#
#   bash scripts/setup-music.sh            # Mac: TensorFold's music3-mlx at a pinned commit in its own venv, the weights (22 GB)
#   bash scripts/setup-music.sh --verify   # check only: no clone, no install, no download
#
# The weights are Comfy-Org/MiniMax-Music-3's single files (DiT fp16 4.9 GB, text encoder pruned bf16 16.7 GB, DAV
# decoder 0.22 GB), under the MiniMax-Music3 Community License: commercial use is allowed, a commercial product must
# show "MiniMax-Music3" in its interface, a company above US$20 million of yearly revenue needs MiniMax's written
# authorisation, and a hosted service must keep safeguards against misuse. Do not redistribute them.
#
# Mac (Apple silicon, 64 GB or more): the engine runs as its own process in $PREFIX/music3-venv, never in the Studio's
# venv. Environment: PREFIX, MUSIC3_SRC (the TensorFold clone, default $PREFIX/music3-src; an existing clone that has
# the pinned commit is used as it is), MUSIC3_VENV ($PREFIX/music3-venv), MUSIC3_MODEL (~/music3-models/MiniMax-Music3-Comfy).
# On a DGX Spark the song runs in the studio's ComfyUI: oneshot-setup-spark.sh --music downloads the same files.
set -euo pipefail

REPO="https://github.com/drowzeys/TensorFold.git"
BRANCH="music3-mlx"; COMMIT="71c7c1f8"
HF_REPO="Comfy-Org/MiniMax-Music-3"
FILES=(diffusion_models/minimax_music3_dit_fp16.safetensors text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors
       vae/minimax_music3_dav.safetensors)
VERIFY=0
for arg in "$@"; do
  case "$arg" in
    --verify) VERIFY=1;;
    *) echo "unknown option $arg" >&2; exit 2;;
  esac
done
die() { echo "FATAL: $*" >&2; exit 1; }
ok()  { echo "  ✓ $*"; }
step(){ echo; echo "==> $*"; }
licence() {
  echo "  MiniMax Music 3 (22 GB) is licensed under the MiniMax-Music3 Community License: commercial use is allowed; a"
  echo "  commercial product must show \"MiniMax-Music3\"; above US\$20M yearly revenue you need MiniMax's written"
  echo "  authorisation; a hosted service must keep misuse safeguards. Read it: https://huggingface.co/$HF_REPO"
}

if [ "$(uname -s)" = Linux ]; then
  echo "On a DGX Spark MiniMax Music 3 runs in the studio's ComfyUI: bash oneshot-setup-spark.sh --music" >&2
  exit 2
fi
[ "$(uname -m)" = arm64 ] || die "the MLX port of MiniMax Music 3 needs Apple silicon"
MEM=$(sysctl -n hw.memsize)
[ "$MEM" -ge $((64 * 1024 * 1024 * 1024)) ] || die "MiniMax Music 3 takes about 33 GB: a Mac with 64 GB or more"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
SRC="${MUSIC3_SRC:-$PREFIX/music3-src}"
VENV="${MUSIC3_VENV:-$PREFIX/music3-venv}"
MODEL="${MUSIC3_MODEL:-$HOME/music3-models/MiniMax-Music3-Comfy}"
PY="$VENV/bin/python"

step "TensorFold $BRANCH @ $COMMIT in $SRC"
if [ -d "$SRC/.git" ]; then
  if git -C "$SRC" merge-base --is-ancestor "$COMMIT" HEAD 2>/dev/null; then
    ok "$SRC is at $(git -C "$SRC" rev-parse --short=8 HEAD) (has $COMMIT)"
  elif [ "$VERIFY" = 1 ]; then
    die "$SRC does not have $COMMIT (run without --verify)"
  elif [ -n "$(git -C "$SRC" status --porcelain)" ]; then
    die "$SRC has local changes and lacks $COMMIT: commit or move them, or set MUSIC3_SRC to another folder"
  else
    git -C "$SRC" fetch -q "$REPO" "$BRANCH" && git -C "$SRC" checkout -q --detach "$COMMIT" && ok "checked out $COMMIT"
  fi
else
  [ "$VERIFY" = 0 ] || die "no TensorFold clone at $SRC"
  git clone -q -b "$BRANCH" "$REPO" "$SRC" && git -C "$SRC" checkout -q --detach "$COMMIT" && ok "cloned"
fi

step "Its own environment: $VENV"
if [ ! -x "$PY" ]; then
  [ "$VERIFY" = 0 ] || die "no environment at $VENV"
  PY311="$(command -v python3.11 || echo /opt/homebrew/bin/python3.11)"
  [ -x "$PY311" ] || die "python3.11 is needed (brew install python@3.11)"
  "$PY311" -m venv "$VENV"
fi
if ! "$PY" -I -c "import tensorfold.music3, tensorfold.families.music3.generate, tokenizers" 2>/dev/null; then
  [ "$VERIFY" = 0 ] || die "the environment lacks tensorfold.music3"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e "$SRC" mlx mlx-lm tokenizers numpy huggingface_hub
fi
"$PY" -I -c "import tensorfold.music3, tensorfold.families.music3.generate, tokenizers" || die "the environment does not import the engine"
ok "tensorfold.music3 ($("$PY" -I -c 'import mlx.core as mx; print("mlx", mx.__version__)'))"

step "The weights: $MODEL"
MISSING=()
for f in "${FILES[@]}"; do [ -s "$MODEL/$f" ] || MISSING+=("$f"); done
if [ "${#MISSING[@]}" = 0 ]; then
  ok "there"
else
  [ "$VERIFY" = 0 ] || die "missing in $MODEL: ${MISSING[*]}"
  licence
  "$VENV/bin/hf" download "$HF_REPO" "${MISSING[@]}" --local-dir "$MODEL" >/dev/null
  for f in "${FILES[@]}"; do [ -s "$MODEL/$f" ] || die "$f did not download"; done
  ok "DiT fp16, text encoder pruned bf16, DAV decoder"
fi

if [ "$VERIFY" = 0 ]; then
  step "A test song (10 s)"
  OUT="$(mktemp -d)/music3-test.wav"
  "$PY" -I -m tensorfold.music3 --model "$MODEL" --caption "Warm acoustic folk, 90 BPM, gentle guitar, soft male vocal." \
    --lyrics "[Verse]
The Music tab is ready now" --seconds 10 --seed 1 --out "$OUT" | tail -1
  [ -s "$OUT" ] || die "the test song wrote nothing"
  ok "$OUT"
fi
echo
echo "Music tab ready (MUSIC3_VENV=$VENV, MUSIC3_MODEL=$MODEL if not the defaults). MiniMax-Music3."
