#!/bin/bash
# NVIDIA Nemotron 3.5 Bolt's engine for a DGX Spark into $PREFIX/bolt: TensorFold 1.1.8's native server
# (tensorfold-native, Zig + CUDA) with MiaAI-Lab's Bolt lane backend (libbolt.so, which runs NVIDIA's LiLiCorr drafter),
# drowzeys/TensorFold branch bolt-lilicorr-118 at BOLT_COMMIT: Mia's recipe (MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-
# DGX-Spark 89e1c8a, MIT, with ChriZzn's speed work) rebased onto TensorFold 1.1.8, its libbolt in zig/bolt-cuda.
# oneshot-setup-spark.sh --bolt runs this; the Studio then runs it as its own brain engine (app/bolt.py, Cuda). Nothing
# here runs a model, and nothing of Docker runs at run time.
#   bash spark/build-bolt.sh            # skips when the pinned build is already installed
# Where the engine comes from, first that works:
#   1. the Studio release's prebuilt archive (BOLT_ASSET_URL, checked against BOLT_ASSET_SHA256), about 40 MB;
#   2. the carrier image's prebuilt payload (/payload/spark-bolt), when it carries this build (checksums);
#   3. BOLT_FROM_SOURCE=1 (or neither of those): the source at BOLT_COMMIT built here with nvcc and Zig 0.17.0, with
#      FFmpeg (video), libjpeg-turbo (pictures) and xgrammar (structured output): about 15 minutes.
# The payload: bin/tensorfold-native and lib/ (libbolt.so, libtfvideo.so, libtfgrammar.so, libturbojpeg.so.0) and the
# licence files. Measured on a GB10 only: elsewhere it refuses unless BOLT_ANY_GPU=1.
# Environment: PREFIX, BOLT_COMMIT, BOLT_ASSET_URL, BOLT_ASSET_SHA256, IMAGE (the carrier), BOLT_FROM_SOURCE, NVCC.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
BOLT_COMMIT="${BOLT_COMMIT:-7d124f36df08a5836d3a5e3e85c19b6d919c70dc}"
BOLT_ASSET_URL="${BOLT_ASSET_URL:-https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/releases/download/v4.2.0/bolt-cuda-${BOLT_COMMIT:0:8}-linux-aarch64.tar.gz}"
BOLT_ASSET_SHA256="${BOLT_ASSET_SHA256:-ed84ec49414d823e9480b88086189f78eefd95d18196a0c29e332b2746698d90}"
CARRIER="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio:4.2.0}"
ZIG_VERSION=0.17.0
OUT="$PREFIX/bolt"
STAMP="$BOLT_COMMIT"
say() { echo "[tensorfold] $*"; }
if [ -x "$OUT/native/bin/tensorfold-native" ] && [ -f "$OUT/native/lib/libbolt.so" ] && [ "$(cat "$OUT/recipe" 2>/dev/null)" = "$STAMP" ]; then
  say "Bolt engine already installed (bolt-lilicorr-118 ${BOLT_COMMIT:0:8})"; exit 0
fi
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gpu.sh"
studio_pin_gpu
GPU="$(studio_smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
case "$GPU" in *GB10*) ;; *)
  [ "${BOLT_ANY_GPU:-0}" = 1 ] || { echo "the Bolt engine is measured on a GB10 (DGX Spark) only; this GPU is '${GPU:-none}'. BOLT_ANY_GPU=1 installs it anyway" >&2; exit 1; };;
esac
[ "$(uname -m)" = aarch64 ] || { echo "the Bolt engine is built for aarch64 (DGX Spark); this machine is $(uname -m): run Bolt through a CUDA build of llama.cpp instead (BOLT_BACKEND=llamacpp, docs/rtx-pro-6000.md)" >&2; exit 1; }
TMP="$PREFIX/bolt.fetch"
rm -rf "$TMP" 2>/dev/null || true
mkdir -p "$TMP"
install_from() {  # $1: a folder holding native/bin/tensorfold-native and native/lib/*.so; $2: where it came from
  local from="$1" what="$2" lib
  [ -x "$from/native/bin/tensorfold-native" ] || return 1
  for lib in libbolt.so libtfvideo.so libtfgrammar.so libturbojpeg.so.0; do
    [ -e "$from/native/lib/$lib" ] || { echo "  the engine from $what has no $lib" >&2; return 1; }
  done
  # a quick check that the binary runs here (no GPU, no weights): its usage line
  case "$(LD_LIBRARY_PATH="$from/native/lib" "$from/native/bin/tensorfold-native" serve --help 2>&1 || true)" in
    *"usage: tensorfold"*) ;; *) echo "  the engine from $what does not run on this machine" >&2; return 1;;
  esac
  rm -rf "$OUT.new" && mkdir -p "$OUT.new"
  cp -a "$from/native" "$OUT.new/native"
  [ ! -d "$from/share" ] || cp -a "$from/share" "$OUT.new/share"
  ( cd "$OUT.new" && find native -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
  echo "$STAMP" > "$OUT.new/recipe"
  echo "$what" > "$OUT.new/source"
  [ ! -d "$OUT/cache" ] || mv "$OUT/cache" "$OUT.new/cache"  # the CUDA kernel cache of earlier loads is kept
  # the engine it replaces stays beside it for a rollback (bolt.previous: mv it back to bolt)
  if [ -d "$OUT" ]; then rm -rf "$OUT.previous" && mv "$OUT" "$OUT.previous"; fi
  mv "$OUT.new" "$OUT"
  say "Bolt engine from $what -> $OUT/native/bin/tensorfold-native"
}
have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }
# 1. the release archive
if [ "${BOLT_FROM_SOURCE:-0}" != 1 ] && [ -n "$BOLT_ASSET_URL" ] && [ "${BOLT_ASSET_SHA256#@}" = "$BOLT_ASSET_SHA256" ]; then
  if curl -fsSL -o "$TMP/bolt.tgz" "$BOLT_ASSET_URL" 2>/dev/null; then
    if [ "$(sha256sum "$TMP/bolt.tgz" | cut -d' ' -f1)" = "$BOLT_ASSET_SHA256" ] && tar -xzf "$TMP/bolt.tgz" -C "$TMP" \
       && [ "$(cat "$TMP/recipe" 2>/dev/null)" = "$STAMP" ] && ( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
       && install_from "$TMP" "$BOLT_ASSET_URL (sha256 verified)"; then rm -rf "$TMP"; exit 0; fi
    echo "  the release archive did not check out (sha256, stamp or checksums); trying the next source" >&2
  fi
  rm -rf "$TMP" && mkdir -p "$TMP"
fi
# 2. the carrier's payload
if [ "${BOLT_FROM_SOURCE:-0}" != 1 ] && have_docker && { docker image inspect "$CARRIER" >/dev/null 2>&1 || docker pull -q "$CARRIER" >/dev/null 2>&1; }; then
  docker run --rm -v "$TMP":/out "$CARRIER" sh -c 'cp -a /payload/spark-bolt/. /out/ 2>/dev/null; chmod -R a+rwX /out' >/dev/null 2>&1 || true
  if [ "$(cat "$TMP/recipe" 2>/dev/null)" = "$STAMP" ] && ( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
     && install_from "$TMP" "$CARRIER (checksums verified)"; then rm -rf "$TMP"; exit 0; fi
  rm -rf "$TMP" && mkdir -p "$TMP"
fi
# 3. from source
NVCC="${NVCC:-}"
[ -n "$NVCC" ] || for candidate in /usr/local/cuda/bin/nvcc "$(command -v nvcc || true)"; do
  [ -x "$candidate" ] && { NVCC="$candidate"; break; }
done
[ -n "$NVCC" ] || { rm -rf "$TMP"; echo "no Bolt engine was installed: the release archive and the carrier had none for ${BOLT_COMMIT:0:8}, and there is no nvcc to build it (CUDA 12.9 or newer; NVCC names one)" >&2; exit 1; }
for tool in git cmake curl make; do command -v "$tool" >/dev/null || { echo "building the Bolt engine needs $tool" >&2; exit 1; }; done
# Zig, checked against the SHA-256 ziglang.org publishes (spark/zig.sh)
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/zig.sh"
ZIG="$(studio_zig "$PREFIX")" || exit 1
SRC="$PREFIX/bolt-src"
DEPS="$PREFIX/bolt-deps"
[ -d "$SRC/.git" ] || git init -q "$SRC"
if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$BOLT_COMMIT" ]; then
  git -C "$SRC" fetch -q --depth 1 https://github.com/drowzeys/TensorFold.git "$BOLT_COMMIT"
  git -C "$SRC" checkout -q FETCH_HEAD
fi
J="${BOLT_JOBS:-4}"
mkdir -p "$DEPS"
say "building the Bolt engine from drowzeys/TensorFold ${BOLT_COMMIT:0:8} (FFmpeg, libjpeg-turbo, xgrammar, then the server and libbolt; about 15 minutes)"
if [ ! -f "$DEPS/ffmpeg/lib/libavcodec.a" ]; then
  sed "s/make -j\"\$(getconf _NPROCESSORS_ONLN)\"/make -j$J/" "$SRC/tools/zig/build_ffmpeg.sh" > "$DEPS/build_ffmpeg.sh"
  nice -n 19 bash "$DEPS/build_ffmpeg.sh" "$DEPS/ffmpeg" > "$DEPS/ffmpeg.log" 2>&1 || { echo "FFmpeg did not build: $DEPS/ffmpeg.log" >&2; exit 1; }
fi
if [ ! -e "$DEPS/jpeg/lib/libturbojpeg.so.0" ] && [ ! -e "$DEPS/jpeg/lib64/libturbojpeg.so.0" ]; then
  curl -fsSL -o "$DEPS/jt.tgz" https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.4.1/libjpeg-turbo-3.1.4.1.tar.gz
  echo "ecae8008e2cc9ade2f2c1bb9d5e6d4fb73e7c433866a056bd82980741571a022  $DEPS/jt.tgz" | sha256sum -c - >/dev/null
  tar -xzf "$DEPS/jt.tgz" -C "$DEPS"
  ( cd "$DEPS/libjpeg-turbo-3.1.4.1" && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_STATIC=0 -DWITH_TURBOJPEG=1 -DCMAKE_SKIP_RPATH=ON \
      -DCMAKE_INSTALL_PREFIX="$DEPS/jpeg" >/dev/null && nice -n 19 cmake --build build -j"$J" >/dev/null && cmake --install build >/dev/null )
fi
tag="$(sed -n 's/^pub const pinned = "\(.*\)";/\1/p' "$SRC/zig/src/platform/grammar_library.zig")"
if [ "$(git -C "$DEPS/xgrammar" describe --tags --exact-match 2>/dev/null)" != "$tag" ]; then
  rm -rf "$DEPS/xgrammar"
  git clone -q --branch "$tag" --recurse-submodules https://github.com/mlc-ai/xgrammar "$DEPS/xgrammar"
fi
BUILD="$TMP/build"
( cd "$SRC" && nice -n 19 "$ZIG" build -Dnvcc="$NVCC" -Dbolt-src="$SRC/zig/bolt-cuda" -Dffmpeg="$DEPS/ffmpeg" -Dxgrammar="$DEPS/xgrammar" \
    -Doptimize=fast -Dstrip=true --prefix "$BUILD" -j"$J" install native bolt video grammar )
jl="$DEPS/jpeg/lib"; [ -d "$jl" ] || jl="$DEPS/jpeg/lib64"
cp -a "$jl"/libturbojpeg.so.0* "$BUILD/native/lib/"
[ -f "$BUILD/native/lib/libtfgrammar.so" ] || cp -a "$BUILD/lib/libtfgrammar.so" "$BUILD/native/lib/"
D="$BUILD/share/doc/tensorfold"
mkdir -p "$D/LICENSES"
for f in LICENSE NOTICE THIRD_PARTY_NOTICES.md; do cp -a "$SRC/$f" "$D/"; done
cp "$SRC/zig/bolt-cuda/LICENSE-MiaAI-Lab" "$D/LICENSES/MiaAI-Lab-libbolt-MIT.txt"
cp "$DEPS/xgrammar/LICENSE" "$D/LICENSES/xgrammar-Apache-2.0.txt"
cp "$DEPS/xgrammar/NOTICE" "$D/LICENSES/xgrammar-NOTICE.txt"
cp "$DEPS/xgrammar/3rdparty/dlpack/LICENSE" "$D/LICENSES/dlpack-Apache-2.0.txt"
if install_from "$BUILD" "a build of drowzeys/TensorFold ${BOLT_COMMIT:0:8} with $NVCC"; then rm -rf "$TMP"; exit 0; fi
rm -rf "$TMP"
echo "no Bolt engine was installed: the build at $BUILD did not check out" >&2
exit 1
