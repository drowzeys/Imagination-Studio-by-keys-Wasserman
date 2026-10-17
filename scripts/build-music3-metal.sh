#!/bin/bash
# Build TensorFold's MiniMax Music 3 engine for Metal (music3-generate, Zig + Metal, no Python) into $PREFIX/music3.
# oneshot-setup.sh --music (scripts/setup-music.sh) runs this on a Mac; the Music tab then runs it as a persistent
# server (app/music.py, Metal), and falls back to the MLX port when it is not built.
#   bash scripts/build-music3-metal.sh     # skips the build when the pinned commit is already installed
# Needs an Apple M5 or later (the engine's DiT, prompt rows and DAV run on its tensor units; an M1 to M4 keeps the MLX
# port) and Xcode's Metal toolchain (xcrun metal; macOS 26: xcodebuild -downloadComponent MetalToolchain). It fetches
# Zig 0.17.0 (unless one is on PATH) and the engine's source (drowzeys/TensorFold at MUSIC3_METAL_COMMIT, branch
# upstream/music3-metal) under $PREFIX. A few minutes.
# Environment: PREFIX, MUSIC3_METAL_COMMIT, MUSIC3_SOURCE (an existing checkout to build instead of fetching one).
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MUSIC3_METAL_COMMIT="${MUSIC3_METAL_COMMIT:-8c9559da70b70650309b36f96e6a30f69cd1607c}"
ZIG_VERSION=0.17.0
OUT="$PREFIX/music3"
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "the Metal music3 engine builds on an Apple-silicon Mac" >&2; exit 1; }
if [ -x "$OUT/bin/music3-generate" ] && [ "$(cat "$OUT/commit" 2>/dev/null)" = "$MUSIC3_METAL_COMMIT" ] && [ -z "${MUSIC3_SOURCE:-}" ]; then
  echo "[tensorfold] music3 Metal engine already built at ${MUSIC3_METAL_COMMIT:0:8}"; exit 0
fi
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
gen="$(sed -n 's/.*Apple M\([0-9]*\).*/\1/p' <<<"$CHIP")"
if [ -n "$gen" ] && [ "$gen" -lt 5 ]; then
  echo "the Metal music3 engine needs an Apple M5 or later; this Mac is '$CHIP' (the Music tab uses the MLX port)" >&2; exit 1
fi
# the Command Line Tools have no Metal compiler: Xcode's, where xcode-select still points at them
if ! xcrun -sdk macosx metal --version >/dev/null 2>&1 && [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
xcrun -sdk macosx metal --version >/dev/null 2>&1 || {
  echo "no Metal compiler: install Xcode, then xcodebuild -downloadComponent MetalToolchain" >&2; exit 1; }
ZIG="$(command -v zig || true)"
if [ -z "$ZIG" ] || [ "$("$ZIG" version)" != "$ZIG_VERSION" ]; then
  ZIG="$PREFIX/zig-$ZIG_VERSION/zig"
  if [ ! -x "$ZIG" ]; then
    echo "[tensorfold] fetching Zig $ZIG_VERSION"
    mkdir -p "$PREFIX/zig-$ZIG_VERSION"
    curl -sfL "https://ziglang.org/download/$ZIG_VERSION/zig-aarch64-macos-$ZIG_VERSION.tar.xz" | tar -xJ --strip-components=1 -C "$PREFIX/zig-$ZIG_VERSION"
  fi
fi
SRC="${MUSIC3_SOURCE:-$PREFIX/music3-metal-src}"
if [ -z "${MUSIC3_SOURCE:-}" ]; then
  [ -d "$SRC/.git" ] || git init -q "$SRC"
  # a checkout already at the commit (an earlier run, or one copied here for a machine without network) is used as it is
  if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$MUSIC3_METAL_COMMIT" ]; then
    git -C "$SRC" fetch -q --depth 1 https://github.com/drowzeys/TensorFold.git "$MUSIC3_METAL_COMMIT"
    git -C "$SRC" checkout -q FETCH_HEAD
  fi
fi
echo "[tensorfold] building the music3 Metal engine with Zig $("$ZIG" version)"
(cd "$SRC" && nice -n 19 "$ZIG" build music3-generate -Doptimize=ReleaseFast)
mkdir -p "$OUT/bin"
cp "$SRC/zig-out/bin/music3-generate" "$OUT/bin/music3-generate"
if [ -n "${MUSIC3_SOURCE:-}" ] && git -C "$SRC" rev-parse HEAD >/dev/null 2>&1; then git -C "$SRC" rev-parse HEAD > "$OUT/commit"
else echo "$MUSIC3_METAL_COMMIT" > "$OUT/commit"; fi
# a quick check that the binary runs on this machine (no GPU work, no weights)
said="$("$OUT/bin/music3-generate" --help 2>&1 || true)"
case "$said" in *"usage: music3-generate"*) ;; *) echo "the built music3-generate does not run: ${said:0:300}" >&2; exit 1;; esac
echo "[tensorfold] music3 Metal engine -> $OUT/bin/music3-generate"
