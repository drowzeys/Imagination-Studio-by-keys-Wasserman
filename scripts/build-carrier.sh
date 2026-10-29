#!/bin/bash
# Build and push the GHCR carrier image, one for both builds: for a Mac the TensorFold wheel at the pinned commit, the
# dependency lock, the render scripts and the native Metal engine; for a DGX Spark the prebuilt CUDA engines
# (payload/spark-engine, copied from $PREFIX/tf-h3 on a Spark where spark/build-engine.sh has run, and
# payload/spark-music3, MiniMax Music 3's, from $PREFIX/music3 where spark/build-music3.sh has run, and
# payload/spark-drama3, Fish Audio Drama 3's, from $PREFIX/drama3 where spark/build-drama3.sh has run: no weights).
# Build it on a worker DGX Spark (arm64 + CUDA) with both engines built; payload/zig-engine comes from a Mac, or from
# the previous carrier when its commit is unchanged (docker run --rm -v "$PWD/payload":/out IMAGE:OLD cp -a /payload/zig-engine /out/).
#   bash scripts/build-carrier.sh           # build only
#   PUSH=1 bash scripts/build-carrier.sh    # build + push (needs `docker login ghcr.io` with write:packages)
# The wheel is pure Python, so the carrier can be built on any machine with Docker and python3. The native engine
# is not: build it on a Mac with scripts/build-zig-engine.sh and copy payload/zig-engine here first; without it the
# carrier is built without the engine and oneshot-setup.sh builds from source or stays on the MLX engine.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio}"
TAG="${TAG:-4.2.1}"
TF_REPO="https://github.com/drowzeys/TensorFold.git"
TF_COMMIT="a2b592fef5366bf16d4729651b6549094981d3fe"
cd "$HERE"
mkdir -p payload
# An engine's debug sections name the folders it was built in (the build machine's home and user). They are never
# loaded, so they are dropped from every binary copied here: the code, data and CUDA kernels stay byte for byte.
nodebug() { local f; for f in "$@"; do strip --strip-debug "$f"; done; }
# WHEEL_DIR: a folder holding the wheel already built at TF_COMMIT (for a worker Spark without network: build it on a
# machine with network, `python3 -m pip wheel --no-deps -w DIR "tensorfold @ git+$TF_REPO@$TF_COMMIT"`, and copy it)
if [ -n "${WHEEL_DIR:-}" ]; then cp "$WHEEL_DIR"/tensorfold-*.whl payload/
else python3 -m pip wheel --no-deps -q -w payload "tensorfold @ git+$TF_REPO@$TF_COMMIT"; fi
cp requirements.lock h3_generate.py qwen_image_generate.py zig_fasth3.py fasth3_q8.py payload/
( cd payload && sha256sum tensorfold-*.whl requirements.lock h3_generate.py qwen_image_generate.py zig_fasth3.py fasth3_q8.py > SHA256SUMS )
if [ -x payload/zig-engine/tf-h3-dit ]; then ( cd payload/zig-engine && sha256sum -c SHA256SUMS ) || { echo "payload/zig-engine does not match its checksums" >&2; exit 1; }
else echo "NOTE: no payload/zig-engine: this carrier will not hold the native engine" >&2; fi
# the DGX Spark's engine, when this machine has built it
SPARK_ENGINE="${PREFIX:-$HOME/.local/opt/tensorfold-studio}/tf-h3"
if [ -s "$SPARK_ENGINE/libtf_h3.so" ]; then
  mkdir -p payload/spark-engine
  cp "$SPARK_ENGINE/libtf_h3.so" "$SPARK_ENGINE/commit" payload/spark-engine/
  nodebug payload/spark-engine/libtf_h3.so
  ( cd payload/spark-engine && sha256sum libtf_h3.so commit > SHA256SUMS )
  echo "spark-engine: libtf_h3.so at $(cut -c1-8 payload/spark-engine/commit) ($(uname -m), sm_121)"
else echo "NOTE: no engine at $SPARK_ENGINE: this carrier will not hold the DGX Spark engine" >&2; fi
# the DGX Spark's MiniMax Music 3 engine (music3-generate, branch music3-cuda), when this machine has built it
SPARK_MUSIC3="${PREFIX:-$HOME/.local/opt/tensorfold-studio}/music3"
if [ -x "$SPARK_MUSIC3/bin/music3-generate" ]; then
  mkdir -p payload/spark-music3
  cp "$SPARK_MUSIC3/bin/music3-generate" "$SPARK_MUSIC3/commit" payload/spark-music3/
  nodebug payload/spark-music3/music3-generate
  ( cd payload/spark-music3 && sha256sum music3-generate commit > SHA256SUMS )
  echo "spark-music3: music3-generate at $(cut -c1-8 payload/spark-music3/commit) ($(uname -m), sm_121)"
else echo "NOTE: no engine at $SPARK_MUSIC3: this carrier will not hold the DGX Spark's music engine" >&2; fi
# the DGX Spark's Fish Audio Drama 3 engine (drama3-generate and libtensorfold_drama3.so, branch drama3-cuda), when
# spark/build-drama3.sh has built it: the engine only, never the converted codec or any of Fish's weights
SPARK_DRAMA3="${PREFIX:-$HOME/.local/opt/tensorfold-studio}/drama3"
if [ -x "$SPARK_DRAMA3/bin/drama3-generate" ] && [ -s "$SPARK_DRAMA3/lib/libtensorfold_drama3.so" ] && [ -s "$SPARK_DRAMA3/commit" ]; then
  rm -rf payload/spark-drama3 && mkdir -p payload/spark-drama3
  cp "$SPARK_DRAMA3/bin/drama3-generate" "$SPARK_DRAMA3/lib/libtensorfold_drama3.so" "$SPARK_DRAMA3/commit" payload/spark-drama3/
  nodebug payload/spark-drama3/drama3-generate payload/spark-drama3/libtensorfold_drama3.so
  ( cd payload/spark-drama3 && sha256sum drama3-generate libtensorfold_drama3.so commit > SHA256SUMS )
  echo "spark-drama3: drama3-generate + libtensorfold_drama3.so at $(cut -c1-8 payload/spark-drama3/commit) ($(uname -m), sm_121)"
else echo "NOTE: no engine at $SPARK_DRAMA3 (or no commit file): this carrier will not hold the DGX Spark's voice engine" >&2; fi
# the DGX Spark's NVIDIA Nemotron 3.5 Bolt engine (tensorfold-native with Mia's libbolt), when spark/build-bolt.sh has
# installed it: native/ and TensorFold's licence files, with the recipe stamp and checksums build-bolt.sh checks
SPARK_BOLT="${PREFIX:-$HOME/.local/opt/tensorfold-studio}/bolt"
if [ -x "$SPARK_BOLT/native/bin/tensorfold-native" ] && [ -f "$SPARK_BOLT/recipe" ]; then
  mkdir -p payload/spark-bolt
  cp -a "$SPARK_BOLT/native" "$SPARK_BOLT/recipe" "$SPARK_BOLT/SHA256SUMS" payload/spark-bolt/
  [ ! -d "$SPARK_BOLT/share" ] || cp -a "$SPARK_BOLT/share" payload/spark-bolt/
  ( cd payload/spark-bolt && sha256sum -c SHA256SUMS >/dev/null ) || { echo "spark-bolt: checksums do not match" >&2; exit 1; }
  echo "spark-bolt: Bolt engine, recipe $(cut -c1-8 payload/spark-bolt/recipe) ($(uname -m), sm_121)"
else echo "NOTE: no engine at $SPARK_BOLT: this carrier will not hold the DGX Spark's Bolt engine" >&2; fi
cat payload/SHA256SUMS
# nothing of this machine's folders or user may ship: a binary built here names them unless its debug sections are gone
leaks="$(grep -rlaF -e "$HOME/" -e "/$(id -un)/" payload 2>/dev/null || true)"
if [ -n "$leaks" ]; then echo "the payload names this machine's folders or user (strip or rebuild these):" >&2; echo "$leaks" >&2; exit 1; fi
if [ "${PUSH:-0}" = 1 ]; then
  docker buildx build --platform linux/arm64,linux/amd64 -f Dockerfile.ghcr -t "$IMAGE:$TAG" -t "$IMAGE:latest" --push .
  docker buildx imagetools inspect "$IMAGE:$TAG" | grep -E '^Digest' || true
else
  docker build -f Dockerfile.ghcr -t "$IMAGE:$TAG" -t "$IMAGE:latest" .
fi
