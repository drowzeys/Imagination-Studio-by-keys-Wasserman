#!/bin/bash
# The ComfyUI server behind the Library actions and MOSS-Image on a Mac (scripts/setup-comfy.sh installs it).
#   bash mac/comfy.sh start     # in the background, on 127.0.0.1:8191 (spark/spark_generate.py does this itself)
#   bash mac/comfy.sh stop      # frees the models' memory
#   bash mac/comfy.sh status
# The same graphs as on a DGX Spark (spark/spark_generate.py, spark/spark_workflows.py) run here on PyTorch's Metal
# device, in their own environment ($PREFIX/comfy-venv), never the Studio's MLX one. Two things differ from a Spark:
# - attention is PyTorch's own (--use-pytorch-cross-attention). Without it ComfyUI on a Mac takes its sub-quadratic
#   attention, which adds a text mask to the scores as a bias and gave wrong pictures for Qwen-Image.
# - MiniMax H3 is read from the Mac's own files in H3_MODEL_DIR through pointer files (mac/studio_files), not copied.
# A kill-guard stands beside the server: it stops it when vm_stat's free, file-backed and purgeable memory falls under
# MAC_COMFY_GUARD_GB (default 16), or macOS says its memory pressure is critical. Inactive pages are not counted: at
# a panic on 2026-10-20 (the window server stalled 125 s) 57 GB stood "inactive" while 15 MB was free.
# Environment: PREFIX, MAC_COMFY_PORT (8191), MAC_COMFY_MODELS ($PREFIX/comfy-models), H3_MODEL_DIR, MAC_COMFY_ARGS.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
PORT="${MAC_COMFY_PORT:-8191}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY="$PREFIX/ComfyUI"
MATCH="[m]ain.py --listen 127.0.0.1 --port $PORT"
case "${1:-start}" in
  stop) pkill -f "$MATCH" 2>/dev/null && echo "stopped" || echo "not running"; rm -f "$PREFIX/comfy.kind";;
  status) pgrep -f "$MATCH" >/dev/null && echo "running on 127.0.0.1:$PORT" || echo "not running";;
  run)
    rm -rf "$COMFY/custom_nodes/studio_files"
    mkdir -p "$COMFY/custom_nodes/studio_files" && cp "$HERE/studio_files/__init__.py" "$COMFY/custom_nodes/studio_files/"
    "$PREFIX/comfy-venv/bin/python" "$HERE/comfy_files.py" "$PREFIX"
    cd "$COMFY"
    # MPS has no fallback for an op it lacks without this; the few the node packs use run on the CPU
    export PYTORCH_ENABLE_MPS_FALLBACK=1
    # shellcheck disable=SC2086
    nice -n 10 "$PREFIX/comfy-venv/bin/python" main.py --listen 127.0.0.1 --port "$PORT" --use-pytorch-cross-attention \
      --extra-model-paths-config "$PREFIX/comfy-models.yaml" ${MAC_COMFY_ARGS:-} &
    pid=$!
    trap 'kill $pid 2>/dev/null' TERM INT
    GUARD="${MAC_COMFY_GUARD_GB:-16}"
    while kill -0 $pid 2>/dev/null; do
      # free, file-backed and purgeable pages: what macOS gets back without compressing (app/telemetry.py)
      free=$(vm_stat | awk '/page size of/ {size=$8} /^(Pages free|File-backed pages|Pages purgeable):/ {gsub("\\.","",$NF); pages+=$NF} END {printf "%d", pages*size/1073741824}')
      level=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)
      if [ "${free:-999}" -lt "$GUARD" ] || [ "${level:-1}" -ge 4 ]; then
        echo "[tensorfold] memory guard: ${free} GB free, pressure level ${level}: stopping ComfyUI"; kill -9 $pid; break
      fi
      sleep 1
    done
    wait $pid 2>/dev/null || true;;
  start)
    pgrep -f "$MATCH" >/dev/null && { echo "already running"; exit 0; }
    [ -x "$PREFIX/comfy-venv/bin/python" ] && [ -f "$COMFY/main.py" ] || { echo "run scripts/setup-comfy.sh first: nothing at $PREFIX/ComfyUI" >&2; exit 1; }
    rm -f "$PREFIX/comfy.kind"
    nohup bash "$0" run > "$PREFIX/comfy.log" 2>&1 < /dev/null &
    echo "ComfyUI starting on 127.0.0.1:$PORT (log: $PREFIX/comfy.log)";;
  *) echo "usage: comfy.sh start|stop|status" >&2; exit 2;;
esac
