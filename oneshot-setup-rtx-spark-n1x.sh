#!/bin/bash
# NVIDIA RTX Spark N1X entry (Windows on Arm; run this inside its WSL2 Ubuntu). The install is the DGX Spark recipe
# (oneshot-setup-spark.sh -> oneshot-setup-cuda.sh): the N1X is sm_121 like a GB10. Its firmware gives the GPU a fixed
# share of the memory (96 of 128 GB on an HP OmniBook X) and leaves Windows and WSL the rest (about 30 GB), so this
# file opts into that split: STUDIO_N1X=1 makes the memory guard use the GPU's carve-out as the pool and flag, rather
# than refuse, a job its estimate says may not fit (STUDIO_MEMORY_GUARD=warn; spark/memory_guard.py). A DGX Spark does
# not use this file.
#   bash oneshot-setup-rtx-spark-n1x.sh --yes --no-render
# Speed: an N1X laptop renders a pass at the DGX Spark's speed only in its maker's performance mode, plugged in
# (measured on an HP OmniBook X 14: 864x480, 124 frames, 7.0 s a pass in HP Performance mode, 9.0 s in Balanced; a GB10
# 6.6 s, the same output byte for byte).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STUDIO_N1X=1
export STUDIO_ALLOW_SMALL_VRAM=1
exec bash "$HERE/oneshot-setup-spark.sh" "$@"
