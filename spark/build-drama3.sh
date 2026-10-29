#!/bin/bash
# Build TensorFold's Fish Audio Drama 3 engine for CUDA (drama3-generate and libtensorfold_drama3.so, Zig + CUDA, no
# Python) into $PREFIX/drama3: bin/drama3-generate, lib/libtensorfold_drama3.so, commit and BUILD.
# oneshot-setup-spark.sh --voice runs this (through scripts/setup-voice.sh); the Voice tab runs the engine as a
# persistent server (app/voice.py, Cuda).
#   bash spark/build-drama3.sh            # skips the build when the pinned commit is already installed
# With Docker it first tries the prebuilt engine in the carrier image (GB10, aarch64; ENGINE_FROM_SOURCE=1 skips that).
# A build needs the CUDA toolkit's nvcc (12.9 or newer; a Spark has 13 at /usr/local/cuda) and fetches Zig 0.17.0 and the
# engine's source (drowzeys/TensorFold at DRAMA3_COMMIT, branch drama3-cuda) under $PREFIX. Its zig build compiles the
# kernel fatbins of TensorFold's whole CUDA engine with nvcc (the runtime embeds them all), for the GPU's own target (sm_121 on a GB10, sm_120 on an RTX Pro 6000): about two minutes
# on a GB10 (1 min 43 s measured, 43 fatbins).
# Measured on a GB10 only: on another GPU it refuses unless DRAMA3_ANY_GPU=1.
# It installs no weights and no codec: Fish's checkpoint and the converted codec are scripts/setup-voice.sh's.
# Environment: PREFIX, NVCC, DRAMA3_COMMIT, DRAMA3_SOURCE (an existing checkout to build instead of fetching one),
# DRAMA3_BUILD_JOBS (parallel compile jobs, default 4: an nvcc job takes a few GB), IMAGE (the carrier).
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
DRAMA3_COMMIT="${DRAMA3_COMMIT:-81173e6c612739a3427844fd8131cf4905e0a9c4}"
ZIG_VERSION=0.17.0
OUT="$PREFIX/drama3"
installed() {
  [ -x "$OUT/bin/drama3-generate" ] && [ -s "$OUT/lib/libtensorfold_drama3.so" ] || return 1
  [ "$(cat "$OUT/commit" 2>/dev/null)" = "$DRAMA3_COMMIT" ] && return 0
  # an engine built by hand before this script (the README's earlier steps) left only BUILD: "TensorFold drama3-cuda <commit>"
  if [ ! -f "$OUT/commit" ] && grep -q "drama3-cuda ${DRAMA3_COMMIT:0:8}" "$OUT/BUILD" 2>/dev/null; then echo "$DRAMA3_COMMIT" > "$OUT/commit"; return 0; fi
  return 1
}
if installed && [ -z "${DRAMA3_SOURCE:-}" ]; then
  echo "[tensorfold] drama3 engine already built at ${DRAMA3_COMMIT:0:8}"; exit 0
fi
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gpu.sh"
studio_pin_gpu
GPU="$(studio_smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
# the GPU's compute capability as nvcc names it: 121 on a GB10 (DGX Spark), 120 on an RTX Pro 6000 Blackwell (a
# cubin for one does not run on the other); TF_SM overrides, a comma list builds both
smi_i=()
[ -z "${STUDIO_GPU:-}" ] || smi_i=(-i "$STUDIO_GPU")
SM="${TF_SM:-$(nvidia-smi ${smi_i[@]+"${smi_i[@]}"} --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' .')}"
case "$SM" in ''|*[!0-9,]*) SM=121;; esac  # a driver without the field answers with words
case "$GPU" in *GB10*) ;; *)
  if [ "$SM" != 120 ]; then
    [ "${DRAMA3_ANY_GPU:-0}" = 1 ] || { echo "the drama3 engine is measured on a GB10 (DGX Spark) only; this GPU is '${GPU:-none}'. DRAMA3_ANY_GPU=1 builds it anyway" >&2; exit 1; }
  fi;;
esac

# put FROM's drama3-generate and libtensorfold_drama3.so in place, with the commit and a BUILD line saying where from
place() {
  local from="$1" commit="$2" how="$3"
  mkdir -p "$OUT/bin" "$OUT/lib"
  cp "$from/drama3-generate" "$OUT/bin/drama3-generate.part" && chmod 755 "$OUT/bin/drama3-generate.part"
  cp "$from/libtensorfold_drama3.so" "$OUT/lib/libtensorfold_drama3.so.part" && chmod 755 "$OUT/lib/libtensorfold_drama3.so.part"
  mv -f "$OUT/bin/drama3-generate.part" "$OUT/bin/drama3-generate"
  mv -f "$OUT/lib/libtensorfold_drama3.so.part" "$OUT/lib/libtensorfold_drama3.so"
  echo "$commit" > "$OUT/commit"
  echo "TensorFold drama3-cuda ${commit:0:8} ($how, $(date '+%Y-%m-%d %H:%M:%S'))" > "$OUT/BUILD"
}

# the prebuilt engine from the carrier image (one already here, else pulled), when Docker is here and the image holds
# this commit's build: its commit file and checksums decide
IMAGE="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio:4.2.0}"
if [ -z "${DRAMA3_SOURCE:-}" ] && [ "${ENGINE_FROM_SOURCE:-0}" != 1 ] && [ "$(uname -m)" = aarch64 ] && command -v docker >/dev/null 2>&1 \
   && docker info >/dev/null 2>&1 && { docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull -q "$IMAGE" >/dev/null 2>&1; }; then
  TMP="$PREFIX/drama3.fetch"
  rm -rf "$TMP" 2>/dev/null || true
  mkdir -p "$TMP"
  docker run --rm -v "$TMP":/out "$IMAGE" sh -c 'cp /payload/spark-drama3/* /out/ 2>/dev/null; chmod a+rw /out/*' || true
  if [ "$(cat "$TMP/commit" 2>/dev/null)" = "$DRAMA3_COMMIT" ] && ( cd "$TMP" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
     && grep -q ' drama3-generate$' "$TMP/SHA256SUMS" && grep -q ' libtensorfold_drama3.so$' "$TMP/SHA256SUMS"; then
    place "$TMP" "$DRAMA3_COMMIT" "prebuilt, from $IMAGE"
    rm -rf "$TMP" 2>/dev/null || true
    echo "[tensorfold] drama3 engine from $IMAGE (checksums verified) -> $OUT"; exit 0
  fi
  echo "[tensorfold] $IMAGE holds no drama3 engine at ${DRAMA3_COMMIT:0:8}: building it from source"
  rm -rf "$TMP" 2>/dev/null || true
fi

NVCC="${NVCC:-}"
[ -n "$NVCC" ] || for candidate in /usr/local/cuda/bin/nvcc "$(command -v nvcc || true)"; do
  [ -x "$candidate" ] && { NVCC="$candidate"; break; }
done
{ [ -n "$NVCC" ] && command -v "$NVCC" >/dev/null 2>&1; } || { echo "no nvcc${NVCC:+ at $NVCC}: install the CUDA toolkit (12.9 or newer) or set NVCC" >&2; exit 1; }
version="$("$NVCC" --version | sed -n 's/.*release \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
read -r major minor <<<"$version"
[ "$major" -gt 12 ] || { [ "$major" = 12 ] && [ "$minor" -ge 9 ]; } || { echo "$NVCC is CUDA $major.$minor; the GB10 needs 12.9 or newer" >&2; exit 1; }
case "$(uname -m)" in aarch64) arch=aarch64;; x86_64) arch=x86_64;; *) echo "no Zig build for $(uname -m)" >&2; exit 1;; esac
# Zig, checked against the SHA-256 ziglang.org publishes (spark/zig.sh)
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/zig.sh"
ZIG="$(studio_zig "$PREFIX")" || exit 1
SRC="${DRAMA3_SOURCE:-$PREFIX/drama3-cuda-src}"
if [ -z "${DRAMA3_SOURCE:-}" ]; then
  [ -d "$SRC/.git" ] || git init -q "$SRC"
  # a checkout already at the commit (an earlier run, or one copied here for a machine without network) is used as it is
  if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$DRAMA3_COMMIT" ]; then
    git -C "$SRC" fetch -q --depth 1 https://github.com/drowzeys/TensorFold.git "$DRAMA3_COMMIT"
    git -C "$SRC" checkout -q FETCH_HEAD
  fi
fi
echo "[tensorfold] building the drama3 engine with $NVCC (every CUDA kernel fatbin for sm_${SM//,/, sm_}, then the engine)"
# stripped: no build machine paths in the binaries (they may go into the carrier image)
(cd "$SRC" && nice -n 19 "$ZIG" build drama3-generate drama3-lib -j"${DRAMA3_BUILD_JOBS:-4}" -Doptimize=ReleaseFast \
  -Dnvcc="$NVCC" -Dsm="$SM" -Dstrip=true --prefix "$SRC/zig-out")
STAGE="$SRC/zig-out/stage"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp "$SRC/zig-out/bin/drama3-generate" "$SRC/zig-out/lib/libtensorfold_drama3.so" "$STAGE/"
if [ -n "${DRAMA3_SOURCE:-}" ] && git -C "$SRC" rev-parse HEAD >/dev/null 2>&1; then commit="$(git -C "$SRC" rev-parse HEAD)"
else commit="$DRAMA3_COMMIT"; fi
place "$STAGE" "$commit" "built here with $("$NVCC" --version | sed -n 's/.*release \([0-9.]*\).*/CUDA \1/p')"
rm -rf "$STAGE"
# a quick check that the binary runs on this machine (no GPU, no weights)
said="$("$OUT/bin/drama3-generate" --help 2>&1 || true)"
case "$said" in *"usage: drama3-generate"*) ;; *) echo "the built drama3-generate does not run: ${said:0:300}" >&2; exit 1;; esac
echo "[tensorfold] drama3 engine -> $OUT (bin/drama3-generate, lib/libtensorfold_drama3.so)"
