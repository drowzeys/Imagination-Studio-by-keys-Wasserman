#!/bin/bash
# NVIDIA Nemotron 3.5 Bolt's engine for a DGX Spark into $PREFIX/bolt: TensorFold's native server (tensorfold-native, Zig
# + CUDA) with Mia's Bolt lane backend (libbolt.so, NVIDIA's LiLiCorr drafter run inside it), from MiaAI-Lab's recipe
# Nemotron-3.5-Bolt-TensorFold-DGX-Spark (MIT) on TensorFold 46a08c18 (Apache 2.0) and its patches.
# oneshot-setup-spark.sh --bolt runs this; the Studio then runs it as its own brain engine (app/bolt.py, Cuda). Nothing
# here runs a model, and nothing of Docker runs at run time.
#   bash spark/build-bolt.sh            # skips when the pinned recipe is already installed
# Where the engine comes from, first that works:
#   1. the carrier image's prebuilt payload (/payload/spark-bolt), when it carries this recipe's build (checksums);
#   2. Mia's own prebuilt image on GHCR (or one already on this machine), for this recipe's commit: its /opt/tensorfold
#      is copied out with `docker create` + `docker cp` (no container runs);
#   3. BOLT_FROM_SOURCE=1: Mia's recipe at BOLT_RECIPE_COMMIT is cloned and its own scripts/prepare.sh builds the image
#      (TensorFold at 46a08c18, patches/, engine/, FFmpeg, libjpeg-turbo, xgrammar), then 2.
# The payload is about 130 MB: bin/tensorfold-native and lib/ (libbolt.so, libtfvideo.so, libtfgrammar.so,
# libturbojpeg.so.0) and TensorFold's licence files. Measured on a GB10 only: elsewhere it refuses unless BOLT_ANY_GPU=1.
# Environment: PREFIX, BOLT_RECIPE_COMMIT, BOLT_IMAGE (an image to copy from instead), BOLT_FROM_SOURCE.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
# MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-DGX-Spark at 6f61d772 (structured output, forced tool calls, libtfgrammar):
# TensorFold 46a08c18 with patches 5e11cf1ed295, the image tag Mia's scripts/config.sh computes for it
BOLT_RECIPE_COMMIT="${BOLT_RECIPE_COMMIT:-6f61d7727ae3fac010f30e240799cc884d295f6e}"
BOLT_PATCHES="${BOLT_PATCHES:-5e11cf1ed295}"
TF_REF=46a08c18608153bfc566703a88acc883bc6140f4
MIA_IMAGE="${BOLT_IMAGE:-ghcr.io/miaai-lab/nemotron-3.5-bolt-tensorfold-dgx-spark:zig-${TF_REF:0:7}-$BOLT_PATCHES}"
LOCAL_IMAGE="tensorfold-nemotron-bolt:zig-${TF_REF:0:7}"
CARRIER="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio:3.1.0}"
OUT="$PREFIX/bolt"
STAMP="$BOLT_RECIPE_COMMIT $BOLT_PATCHES"
say() { echo "[tensorfold] $*"; }
if [ -x "$OUT/native/bin/tensorfold-native" ] && [ -f "$OUT/native/lib/libbolt.so" ] && [ "$(cat "$OUT/recipe" 2>/dev/null)" = "$STAMP" ]; then
  say "Bolt engine already installed (recipe ${BOLT_RECIPE_COMMIT:0:8}, patches $BOLT_PATCHES)"; exit 0
fi
GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
case "$GPU" in *GB10*) ;; *)
  [ "${BOLT_ANY_GPU:-0}" = 1 ] || { echo "Mia's Bolt engine is measured on a GB10 (DGX Spark) only; this GPU is '${GPU:-none}'. BOLT_ANY_GPU=1 installs it anyway" >&2; exit 1; };;
esac
[ "$(uname -m)" = aarch64 ] || { echo "the prebuilt Bolt engine is for aarch64 (DGX Spark); this machine is $(uname -m)" >&2; exit 1; }
TMP="$PREFIX/bolt.fetch"
rm -rf "$TMP" 2>/dev/null || true
mkdir -p "$TMP"
install_from() {  # $1: a folder holding native/bin/tensorfold-native and native/lib/*.so; $2: where it came from
  local from="$1" what="$2"
  [ -x "$from/native/bin/tensorfold-native" ] && [ -f "$from/native/lib/libbolt.so" ] || return 1
  # a quick check that the binary runs here (no GPU, no weights): its usage line
  case "$(LD_LIBRARY_PATH="$from/native/lib" "$from/native/bin/tensorfold-native" --help 2>&1 || true)" in
    *"usage: tensorfold-native"*) ;; *) echo "  the engine from $what does not run on this machine" >&2; return 1;;
  esac
  rm -rf "$OUT.new" && mkdir -p "$OUT.new"
  cp -a "$from/native" "$OUT.new/native"
  [ ! -d "$from/share" ] || cp -a "$from/share" "$OUT.new/share"
  ( cd "$OUT.new" && find native -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
  echo "$STAMP" > "$OUT.new/recipe"
  echo "$what" > "$OUT.new/source"
  [ ! -d "$OUT/cache" ] || mv "$OUT/cache" "$OUT.new/cache"  # the CUDA kernel cache of earlier loads is kept
  rm -rf "$OUT" && mv "$OUT.new" "$OUT"
  say "Bolt engine from $what -> $OUT/native/bin/tensorfold-native"
}
have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }
copy_image() {  # $1: an image holding /opt/tensorfold
  local cid
  cid="$(docker create "$1" 2>/dev/null)" || return 1
  docker cp "$cid:/opt/tensorfold/." "$TMP/" >/dev/null 2>&1 || { docker rm "$cid" >/dev/null 2>&1; return 1; }
  docker rm "$cid" >/dev/null 2>&1 || true
  [ "$(docker image inspect -f '{{index .Config.Labels "tf.patches"}}' "$1" 2>/dev/null)" = "$BOLT_PATCHES" ] || {
    echo "  $1 is not this recipe's build (patches $(docker image inspect -f '{{index .Config.Labels "tf.patches"}}' "$1" 2>/dev/null), want $BOLT_PATCHES)" >&2; return 1; }
}
# 1. the carrier's payload
if [ "${BOLT_FROM_SOURCE:-0}" != 1 ] && have_docker && { docker image inspect "$CARRIER" >/dev/null 2>&1 || docker pull -q "$CARRIER" >/dev/null 2>&1; }; then
  docker run --rm -v "$TMP":/out "$CARRIER" sh -c 'cp -a /payload/spark-bolt/. /out/ 2>/dev/null; chmod -R a+rwX /out' >/dev/null 2>&1 || true
  if [ "$(cat "$TMP/recipe" 2>/dev/null)" = "$STAMP" ] && ( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
     && install_from "$TMP" "$CARRIER (checksums verified)"; then rm -rf "$TMP"; exit 0; fi
  rm -rf "$TMP" && mkdir -p "$TMP"
fi
# 3. from source: Mia's own build of her image, then 2 copies it out
if [ "${BOLT_FROM_SOURCE:-0}" = 1 ]; then
  have_docker || { echo "BOLT_FROM_SOURCE=1 builds with Mia's scripts/prepare.sh, which needs Docker with the NVIDIA runtime" >&2; exit 1; }
  SRC="$PREFIX/bolt-recipe-src"
  [ -d "$SRC/.git" ] || git init -q "$SRC"
  if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$BOLT_RECIPE_COMMIT" ]; then
    git -C "$SRC" fetch -q --depth 1 https://github.com/MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-DGX-Spark.git "$BOLT_RECIPE_COMMIT"
    git -C "$SRC" checkout -q FETCH_HEAD
  fi
  say "building Mia's Bolt image from source (TensorFold ${TF_REF:0:8} + patches; tens of minutes)"
  ( cd "$SRC" && PULL=0 ./scripts/prepare.sh --rebuild )
  MIA_IMAGE="$LOCAL_IMAGE"
fi
# 2. Mia's image: one already here (the recipe's own start.sh builds or pulls it), else pulled from GHCR
if have_docker; then
  for image in "$MIA_IMAGE" "$LOCAL_IMAGE"; do
    if docker image inspect "$image" >/dev/null 2>&1 || { [ "$image" = "$MIA_IMAGE" ] && say "pulling $image" && docker pull -q "$image" >/dev/null 2>&1; }; then
      if copy_image "$image" && install_from "$TMP" "$image"; then rm -rf "$TMP"; exit 0; fi
      rm -rf "$TMP" && mkdir -p "$TMP"
    fi
  done
fi
rm -rf "$TMP"
echo "no Bolt engine was installed: the carrier has none for recipe ${BOLT_RECIPE_COMMIT:0:8}, and Mia's image could not be read" \
     "(Docker is used only to copy the engine out of it). BOLT_FROM_SOURCE=1 builds it, or put a build in $OUT/native" >&2
exit 1
