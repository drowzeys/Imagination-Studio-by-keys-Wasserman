#!/bin/bash
# Build the native engine payload for FastH3: TensorFold 1.0's Zig + Metal runtime with the H3 family, as one small
# binary (its Metal kernels are inside it and compiled when it starts) plus the two Python tools that feed it.
#   bash scripts/build-zig-engine.sh                    # clone the fork at the pinned commit, build, write payload/zig-engine
#   ZIG_SRC=~/src/TensorFold bash scripts/build-zig-engine.sh   # build a checkout you already have (its HEAD is recorded)
# Needs a Mac, zig 0.17 (brew install zig) and Xcode's Metal toolchain. The carrier image ships this payload so that
# oneshot-setup.sh needs neither; scripts/build-carrier.sh picks it up from payload/zig-engine.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_REPO="${TF_REPO:-https://github.com/drowzeys/TensorFold.git}"
ZIG_COMMIT="${ZIG_COMMIT:-b19753ab01beedb04b3b691c9d50023ccfe09f62}"
export PATH="/opt/homebrew/bin:$PATH"
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo "the engine builds on Apple silicon macOS only" >&2; exit 1; }
command -v zig >/dev/null || { echo "zig 0.17 required: brew install zig" >&2; exit 1; }
if [ -n "${ZIG_SRC:-}" ]; then
  SRC="$ZIG_SRC"; ZIG_COMMIT="$(git -C "$SRC" rev-parse HEAD)"
else
  SRC="$(mktemp -d)/TensorFold"
  git init -q "$SRC" && git -C "$SRC" remote add origin "$TF_REPO"
  git -C "$SRC" fetch -q --depth 1 origin "$ZIG_COMMIT" && git -C "$SRC" checkout -q FETCH_HEAD
fi
( cd "$SRC" && zig build tf-h3-dit -Dcpu=apple_m1 -Doptimize=ReleaseFast )
OUT="$HERE/payload/zig-engine"; rm -rf "$OUT"; mkdir -p "$OUT"
cp "$SRC/zig-out/bin/tf-h3-dit" "$SRC/tools/zig/h3_case.py" "$SRC/tools/zig/h3_render.py" "$OUT/"
cp "$SRC/LICENSE" "$SRC/NOTICE" "$SRC/THIRD_PARTY_NOTICES.md" "$OUT/"; cp -R "$SRC/LICENSES" "$OUT/"
echo "$ZIG_COMMIT" > "$OUT/COMMIT"
( cd "$OUT" && shasum -a 256 tf-h3-dit h3_case.py h3_render.py COMMIT > SHA256SUMS )
echo "built $OUT (tf-h3-dit @ ${ZIG_COMMIT:0:8}, $(du -sh "$OUT" | cut -f1))"
