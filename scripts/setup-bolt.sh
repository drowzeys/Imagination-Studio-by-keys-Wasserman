#!/usr/bin/env bash
# NVIDIA Nemotron 3.5 Bolt as the Studio's own brain engine, optional (oneshot-setup.sh --bolt on a Mac,
# oneshot-setup-spark.sh --bolt on a DGX Spark run this). The Studio then loads it on the first brain or agent question
# and unloads it when idle or when a render needs its memory (app/bolt.py).
#
#   bash scripts/setup-bolt.sh            # the weights (about 25 GB), and on a DGX Spark the engine (spark/build-bolt.sh)
#   bash scripts/setup-bolt.sh --verify   # check only: no download, no install
#
# The weights: NVIDIA's official 24 GB checkpoint, nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB at a326822a (22.4 GiB,
# IQ2_XXS experts, NVFP4 Mamba projections, Q8_0 attention), and its LiLiCorr drafter,
# nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr at 4c6a5613 (about 1 GB), both under the OpenMDW License
# Agreement 1.1: use, modification and redistribution are free, commercial use included; a copy passed on keeps the
# agreement and NVIDIA's notices; no warranty.
# DGX Spark: the engine is TensorFold's native server with Mia's Bolt backend (MiaAI-Lab's recipe, MIT), installed into
#   $PREFIX/bolt by spark/build-bolt.sh. It needs about 43 GB free to load and holds about 28 GB.
# Mac: TensorFold's Metal Bolt engine (branch nemotron-bolt-metal), prebuilt, a checksummed release download into
#   $PREFIX/bolt-metal (5 MB). Without it, BOLT_BACKEND=llamacpp runs Bolt through llama.cpp's server with NVIDIA's GGUF
#   (BOLT_LLAMACPP_BIN, BOLT_GGUF), which this script checks but does not install.
# Environment: PREFIX, BOLT_MODELS (default $SPARK_MODELS on a Spark, ~/bolt-models on a Mac).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MODEL_REPO=nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB; MODEL_REVISION=a326822af111290f2af5ac59ef4b5040347dabc4
DRAFT_REPO=nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr; DRAFT_REVISION=4c6a5613b6d887194a3a5493e3a18a5b3930bb86
if [ "$(uname -s)" = Linux ]; then MODELS="${BOLT_MODELS:-${SPARK_MODELS:-$HOME/spark-studio-models}}"; else MODELS="${BOLT_MODELS:-$HOME/bolt-models}"; fi
# Mac: TensorFold's Metal Bolt engine (tensorfold-native with libbolt for Apple GPUs), prebuilt as a release asset
METAL_COMMIT="b4edaea53e3c0624e9fce06f0c9f33150e58cde3"
METAL_URL="https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/releases/download/v3.1.0/bolt-metal-b4edaea5-macos-arm64.tar.gz"
METAL_SHA256="58203b65ec8d7dce6c87b06c7889967583d6148ecb13fcada2da754c384e8a49"
CKPT="$MODELS/Nemotron-3.5-Bolt-24GB"; DRAFT="$MODELS/Nemotron-3.5-Bolt-24GB-LiLiCorr"
VERIFY=0
for arg in "$@"; do
  case "$arg" in
    --verify) VERIFY=1;;
    *) echo "unknown option $arg" >&2; exit 2;;
  esac
done
ok()  { echo "  ✓ $*"; }
step(){ echo; echo "==> $*"; }
die() { echo "  ✗ $*" >&2; exit 1; }
checkpoint_ready() { python3 -I -c '
import json, os, sys
d = sys.argv[1]
if not all(os.path.isfile(os.path.join(d, f)) for f in ("config.json", "tokenizer.json", "model.safetensors.index.json")): sys.exit(1)
shards = set(json.load(open(os.path.join(d, "model.safetensors.index.json")))["weight_map"].values())
sys.exit(0 if shards and all(os.path.isfile(os.path.join(d, f)) for f in shards) else 1)' "$1"; }
drafter_ready() { [ -s "$1/model.safetensors" ] && [ -s "$1/config.json" ]; }

step "NVIDIA Nemotron 3.5 Bolt: the 24 GB checkpoint and the LiLiCorr drafter (about 25 GB) -> $MODELS"
if [ "$VERIFY" = 0 ]; then
  echo "  NVIDIA Nemotron 3.5 Bolt (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB at ${MODEL_REVISION:0:8}, 22.4 GiB, and its"
  echo "  LiLiCorr drafter at ${DRAFT_REVISION:0:8}, about 1 GB) is licensed under the OpenMDW License Agreement 1.1: use,"
  echo "  modification and redistribution are free, commercial use included; a copy passed on keeps the agreement and"
  echo "  NVIDIA's notices; no warranty. Read it: https://huggingface.co/$MODEL_REPO"
fi
if checkpoint_ready "$CKPT" && drafter_ready "$DRAFT"; then
  ok "checkpoint and drafter in $MODELS"
elif [ "$VERIFY" = 1 ]; then
  die "missing in $MODELS: $(checkpoint_ready "$CKPT" || echo "the checkpoint ") $(drafter_ready "$DRAFT" || echo "the drafter") (run without --verify)"
else
  mkdir -p "$MODELS"
  FREE=$(df -Pk "$MODELS" | awk 'NR==2 {print int($4 / 1048576)}')
  [ "$FREE" -ge 30 ] || die "$MODELS has ${FREE} GB free; Bolt needs about 25 GB (30 with room)"
  HF="$PREFIX/venv/bin/hf"; [ -x "$HF" ] || HF="$(command -v hf || true)"
  [ -n "$HF" ] || die "no hf command (the Studio's venv has one: run the Studio's setup first, or pip install huggingface_hub)"
  checkpoint_ready "$CKPT" || "$HF" download "$MODEL_REPO" --revision "$MODEL_REVISION" --local-dir "$CKPT" >/dev/null
  checkpoint_ready "$CKPT" || die "the checkpoint did not download completely: run this again (it resumes)"
  drafter_ready "$DRAFT" || "$HF" download "$DRAFT_REPO" --revision "$DRAFT_REVISION" --local-dir "$DRAFT" >/dev/null
  drafter_ready "$DRAFT" || die "the drafter did not download completely: run this again (it resumes)"
  echo "$MODEL_REVISION" > "$CKPT/.revision"; echo "$DRAFT_REVISION" > "$DRAFT/.revision"
  ok "checkpoint ${MODEL_REVISION:0:8} and drafter ${DRAFT_REVISION:0:8} in $MODELS"
fi

if [ "$(uname -s)" = Linux ]; then
  step "The Bolt engine for CUDA (TensorFold with Mia's Bolt backend) -> $PREFIX/bolt"
  if [ "$VERIFY" = 1 ]; then
    [ -x "$PREFIX/bolt/native/bin/tensorfold-native" ] && ok "tensorfold-native with libbolt (recipe $(cut -c1-8 "$PREFIX/bolt/recipe" 2>/dev/null))" \
      || die "not installed: bash spark/build-bolt.sh"
  else
    PREFIX="$PREFIX" bash "$HERE/spark/build-bolt.sh" || die "the engine was not installed (spark/build-bolt.sh says why)"
  fi
  echo "  The Studio runs it as its brain when Settings → Connections leaves the local server's address empty (or picks"
  echo "  \"Studio engine · Bolt\"). BOLT_MODELS=$MODELS if you moved the weights; BOLT_IDLE_S=never on a head that never renders."
else
  step "The Bolt engine on this Mac: TensorFold's Metal engine (branch nemotron-bolt-metal at ${METAL_COMMIT:0:8}) -> $PREFIX/bolt-metal"
  metal_ok() { [ -x "$PREFIX/bolt-metal/native/bin/tensorfold-native" ] && [ "$(cat "$PREFIX/bolt-metal/COMMIT" 2>/dev/null)" = "$METAL_COMMIT" ] \
               && ( cd "$PREFIX/bolt-metal" && shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 ); }
  if ! metal_ok && [ "$VERIFY" = 0 ]; then  # the prebuilt engine, a checksummed release download (5 MB)
    T="$(mktemp -d)"
    if curl -fsSL -o "$T/metal.tgz" "$METAL_URL" && [ "$(shasum -a 256 "$T/metal.tgz" | cut -d' ' -f1)" = "$METAL_SHA256" ] \
       && tar -xzf "$T/metal.tgz" -C "$T"; then
      mkdir -p "$PREFIX"
      [ ! -e "$PREFIX/bolt-metal" ] || mv "$PREFIX/bolt-metal" "$T/bolt-metal.before"  # an older one goes with the download's folder
      mv "$T/bolt-metal" "$PREFIX/bolt-metal"
    else echo "  ! the Metal engine did not download from $METAL_URL"; fi
    rm -r "$T"
  fi
  if metal_ok; then
    ok "TensorFold's Metal Bolt engine in $PREFIX/bolt-metal (BOLT_BACKEND=metal, the default; measured on an M5 Ultra)"
  else
    echo "  ! no Metal Bolt engine in $PREFIX/bolt-metal. The Studio can run Bolt through llama.cpp's server instead:"
    echo "  BOLT_BACKEND=llamacpp, with BOLT_LLAMACPP_BIN (a llama-server built"
    echo "  with Metal) and BOLT_GGUF (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF; same licence)."
    BIN="${BOLT_LLAMACPP_BIN:-$HOME/bolt/llama.cpp/build/bin/llama-server}"
    [ -x "$BIN" ] && ok "llama-server at $BIN" || echo "  - no llama-server at $BIN"
    GGUF="${BOLT_GGUF:-$(ls "$HOME"/bolt/models/*Bolt*.gguf 2>/dev/null | head -1)}"
    [ -n "$GGUF" ] && [ -f "$GGUF" ] && ok "GGUF at $GGUF" || echo "  - no Bolt GGUF (BOLT_GGUF names one)"
  fi
fi
