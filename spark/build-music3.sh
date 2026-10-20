#!/bin/bash
# Build TensorFold's MiniMax Music 3 engine for CUDA (music3-generate, Zig + CUDA, no Python) into $PREFIX/music3.
# oneshot-setup-spark.sh --music runs this; the Music tab then runs it as a persistent server (app/music.py, Cuda).
#   bash spark/build-music3.sh            # skips the build when the pinned commit is already installed
# With Docker it first tries the prebuilt engine in the carrier image (GB10, aarch64; ENGINE_FROM_SOURCE=1 skips that).
# A build needs the CUDA toolkit's nvcc (12.9 or newer; a Spark has 13 at /usr/local/cuda) and fetches Zig 0.17.0 and the
# engine's source (drowzeys/TensorFold at MUSIC3_COMMIT, branch music3-cuda) under $PREFIX. A few minutes.
# Measured on a GB10 only: on another GPU it refuses unless MUSIC3_ANY_GPU=1 (the Music tab then uses ComfyUI's nodes).
# Environment: PREFIX, NVCC, MUSIC3_COMMIT, MUSIC3_SOURCE (an existing checkout to build instead of fetching one).
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MUSIC3_COMMIT="${MUSIC3_COMMIT:-6b714531e2d60f91d26e98e414547ad94ad771bf}"
ZIG_VERSION=0.17.0
OUT="$PREFIX/music3"
if [ -x "$OUT/bin/music3-generate" ] && [ "$(cat "$OUT/commit" 2>/dev/null)" = "$MUSIC3_COMMIT" ] && [ -z "${MUSIC3_SOURCE:-}" ]; then
  echo "[tensorfold] music3 engine already built at ${MUSIC3_COMMIT:0:8}"; exit 0
fi
GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
case "$GPU" in *GB10*) ;; *)
  [ "${MUSIC3_ANY_GPU:-0}" = 1 ] || { echo "the music3 engine is measured on a GB10 (DGX Spark) only; this GPU is '${GPU:-none}'. MUSIC3_ANY_GPU=1 builds it anyway" >&2; exit 1; };;
esac
# the prebuilt engine from the carrier image (one already here, else pulled), when Docker is here and the image holds
# this commit's build: its commit file and checksums decide
IMAGE="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio:3.1.0}"
if [ -z "${MUSIC3_SOURCE:-}" ] && [ "${ENGINE_FROM_SOURCE:-0}" != 1 ] && [ "$(uname -m)" = aarch64 ] && command -v docker >/dev/null 2>&1 \
   && docker info >/dev/null 2>&1 && { docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q "$IMAGE" >/dev/null 2>&1; }; then
  TMP="$PREFIX/music3.fetch"
  rm -rf "$TMP" 2>/dev/null || true
  mkdir -p "$TMP"
  docker run --rm -v "$TMP":/out "$IMAGE" sh -c 'cp /payload/spark-music3/* /out/ 2>/dev/null; chmod a+rw /out/*' || true
  if [ "$(cat "$TMP/commit" 2>/dev/null)" = "$MUSIC3_COMMIT" ] && ( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ); then
    mkdir -p "$OUT/bin"
    cp "$TMP/music3-generate" "$OUT/bin/" && chmod 755 "$OUT/bin/music3-generate"
    cp "$TMP/commit" "$OUT/"
    rm -rf "$TMP" 2>/dev/null || true
    echo "[tensorfold] music3 engine from $IMAGE (checksums verified) -> $OUT/bin/music3-generate"; exit 0
  fi
  rm -rf "$TMP" 2>/dev/null || true
fi
NVCC="${NVCC:-}"
[ -n "$NVCC" ] || for candidate in /usr/local/cuda/bin/nvcc "$(command -v nvcc || true)"; do
  [ -x "$candidate" ] && { NVCC="$candidate"; break; }
done
[ -n "$NVCC" ] || { echo "no nvcc: install the CUDA toolkit (12.9 or newer) or set NVCC" >&2; exit 1; }
version="$("$NVCC" --version | sed -n 's/.*release \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
read -r major minor <<<"$version"
[ "$major" -gt 12 ] || { [ "$major" = 12 ] && [ "$minor" -ge 9 ]; } || { echo "$NVCC is CUDA $major.$minor; the GB10 needs 12.9 or newer" >&2; exit 1; }
case "$(uname -m)" in aarch64) arch=aarch64;; x86_64) arch=x86_64;; *) echo "no Zig build for $(uname -m)" >&2; exit 1;; esac
ZIG="$PREFIX/zig-$ZIG_VERSION/zig"
if [ ! -x "$ZIG" ]; then
  echo "[tensorfold] fetching Zig $ZIG_VERSION"
  mkdir -p "$PREFIX/zig-$ZIG_VERSION"
  curl -sfL "https://ziglang.org/download/$ZIG_VERSION/zig-$arch-linux-$ZIG_VERSION.tar.xz" | tar -xJ --strip-components=1 -C "$PREFIX/zig-$ZIG_VERSION"
fi
SRC="${MUSIC3_SOURCE:-$PREFIX/music3-cuda-src}"
if [ -z "${MUSIC3_SOURCE:-}" ]; then
  [ -d "$SRC/.git" ] || git init -q "$SRC"
  # a checkout already at the commit (an earlier run, or one copied here for a machine without network) is used as it is
  if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$MUSIC3_COMMIT" ]; then
    git -C "$SRC" fetch -q --depth 1 https://github.com/drowzeys/TensorFold.git "$MUSIC3_COMMIT"
    git -C "$SRC" checkout -q FETCH_HEAD
  fi
fi
echo "[tensorfold] building the music3 engine with $NVCC"
# two compile jobs: the kernel image's compiler takes a few GB
(cd "$SRC" && nice -n 19 "$ZIG" build music3-generate -j2 -Doptimize=ReleaseFast -Dnvcc="$NVCC")
mkdir -p "$OUT/bin"
cp "$SRC/zig-out/bin/music3-generate" "$OUT/bin/music3-generate"
if [ -n "${MUSIC3_SOURCE:-}" ] && git -C "$SRC" rev-parse HEAD >/dev/null 2>&1; then git -C "$SRC" rev-parse HEAD > "$OUT/commit"
else echo "$MUSIC3_COMMIT" > "$OUT/commit"; fi
# a quick check that the binary runs on this machine (no GPU, no weights)
said="$("$OUT/bin/music3-generate" --help 2>&1 || true)"
case "$said" in *"usage: music3-generate"*) ;; *) echo "the built music3-generate does not run: ${said:0:300}" >&2; exit 1;; esac
echo "[tensorfold] music3 engine -> $OUT/bin/music3-generate"
