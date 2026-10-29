#!/bin/bash
# Zig 0.17.0 for the engine builds, sourced by spark/build-*.sh: studio_zig PREFIX puts it at PREFIX/zig-0.17.0/zig
# (once) and prints that path. The archive is checked against the SHA-256 ziglang.org publishes for it
# (ziglang.org/download/index.json) before it is unpacked; one that does not match is deleted and not used.
STUDIO_ZIG_VERSION=0.17.0
STUDIO_ZIG_SHA256_aarch64=9e8d11661d4ae3bd57702a3832781e23ad151dde5798e16a5ccd503f65234ff8
STUDIO_ZIG_SHA256_x86_64=1cbe9df9f27e6b78d14ccbca43b6703a404ef79ef1c463de901d7f088d4e2026
studio_zig() {
  local prefix="$1" arch sum dir archive
  case "$(uname -m)" in aarch64) arch=aarch64;; x86_64) arch=x86_64;; *) echo "no Zig build for $(uname -m)" >&2; return 1;; esac
  dir="$prefix/zig-$STUDIO_ZIG_VERSION"
  if [ ! -x "$dir/zig" ]; then
    echo "[tensorfold] fetching Zig $STUDIO_ZIG_VERSION" >&2
    mkdir -p "$dir"
    archive="$dir.tar.xz.part"
    sum="STUDIO_ZIG_SHA256_$arch"
    curl -sfL -o "$archive" "https://ziglang.org/download/$STUDIO_ZIG_VERSION/zig-$arch-linux-$STUDIO_ZIG_VERSION.tar.xz" \
      || { rm -f "$archive"; echo "Zig $STUDIO_ZIG_VERSION could not be downloaded" >&2; return 1; }
    echo "${!sum}  $archive" | sha256sum -c --quiet - \
      || { rm -f "$archive"; echo "Zig $STUDIO_ZIG_VERSION's archive does not match its published SHA-256: not used" >&2; return 1; }
    tar -xJf "$archive" --strip-components=1 -C "$dir" && rm -f "$archive" || { echo "Zig $STUDIO_ZIG_VERSION's archive does not unpack" >&2; return 1; }
  fi
  echo "$dir/zig"
}
