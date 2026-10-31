#!/bin/bash
# The ComfyUI server behind TensorFold Studio on a DGX Spark.
#   bash spark/comfy.sh start     # in the background, on 127.0.0.1:8190 (spark_generate.py does this itself)
#   bash spark/comfy.sh stop      # frees the models' memory
#   bash spark/comfy.sh status
# It runs at low priority with the memory guard beside it (spark/memory_guard.py watch), whatever started it: on a
# Spark the GPU and the system share one pool, and a machine that runs out can become unreachable. The guard looks
# twenty times a second; it gives the model files' page cache back when little memory is free (the GPU driver takes
# from what is free, not from what is available) and stops the server when available memory is under
# SPARK_MEMORY_FLOOR_MB (default: a tenth of the machine, 12.4 GB on a Spark) or is falling there within a second.
# Environment: PREFIX, SPARK_COMFY_PORT (8190), SPARK_MEMORY_FLOOR_MB, SPARK_MEMORY_FREE_MB (free memory under which
# the cache is given back: two thirds of the floor), SPARK_COMFY_ARGS (ComfyUI flags, default --use-ck-attention). TF_H3_CHECK=1 and TF_H3_PROFILE=1 reach TensorFold's H3 node (see its file).
# SPARK_GOVERNOR names a machine-wide workload governor's folder (spark/workload.py): the server then starts only
# inside a reservation of its own (spark/managed_comfy.py), with the same memory guard beside it and the governor's
# telemetry as well, and `stop` ends that server. Unset, nothing changes.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
PORT="${SPARK_COMFY_PORT:-8190}"
# The default port keeps the old names. Another port is a second studio on this install.
if [ "$PORT" = 8190 ]; then COMFY_LOG="$PREFIX/comfy.log"; COMFY_KIND="$PREFIX/comfy.kind"
else COMFY_LOG="$PREFIX/comfy-$PORT.log"; COMFY_KIND="$PREFIX/comfy-$PORT.kind"; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/gpu.sh"
studio_pin_gpu
MATCH="[m]ain.py --listen 127.0.0.1 --port $PORT"
case "${1:-start}" in
  stop)
    if [ -n "${SPARK_GOVERNOR:-}" ]; then
      # the managed server first, and its wrapper is given the time to end its reservation itself; then only a server
      # process still on this port is killed (the wrapper's own command line names the port too)
      python3 "$HERE/managed_comfy.py" stop || true
      for _ in $(seq 1 25); do pgrep -f "$MATCH" >/dev/null || break; sleep 1; done
      MATCH="^[^ ]+ main\.py --listen 127\.0\.0\.1 --port $PORT( |\$)"
    fi
    pkill -f "$MATCH" 2>/dev/null && echo "stopped" || echo "not running"; rm -f "$COMFY_KIND"
    # a two-GPU world's shared memory (spark/tensorfold_h3/tp.py) is RAM until it is unlinked. Its name carries its
    # ComfyUI's pid (tf-h3-<pid>-<id>): only worlds whose server is gone are removed (another Studio's stay)
    for _ in $(seq 1 10); do pgrep -f "$MATCH" >/dev/null || break; sleep 1; done
    for SHM in /dev/shm/tf-h3-*; do
      [ -e "$SHM" ] || continue
      OWNER="${SHM#/dev/shm/tf-h3-}"; OWNER="${OWNER%%-*}"
      case "$OWNER" in ''|*[!0-9]*) continue;; esac
      kill -0 "$OWNER" 2>/dev/null || rm -f "$SHM"
    done;;
  status) pgrep -f "$MATCH" >/dev/null && echo "running on 127.0.0.1:$PORT" || echo "not running";;
  run)
    # A warm server belongs to the GPU session, not to the first job that loaded it.
    unset STUDIO_GPU_JOB_KEY
    export STUDIO_GPU_MODEL_RAM=1
    export PATH="/usr/local/cuda/bin:$PATH" CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}" MAX_JOBS="${MAX_JOBS:-2}"
    # TensorFold's H3 engine as a ComfyUI node, when it has been built (spark/build-engine.sh)
    NODE="$PREFIX/ComfyUI/custom_nodes/tensorfold_h3"
    if [ -s "$PREFIX/tf-h3/libtf_h3.so" ]; then
      mkdir -p "$NODE"
      # the node, and the two-GPU world beside it (tp.py, tp_rank.py: rank 1 on the second card)
      for PYFILE in "$HERE/tensorfold_h3/"*.py; do
        cp "$PYFILE" "$NODE/$(basename "$PYFILE").$$" && mv -f "$NODE/$(basename "$PYFILE").$$" "$NODE/$(basename "$PYFILE")"
      done
      export TF_H3_LIB="$PREFIX/tf-h3/libtf_h3.so"
    fi
    # two cards: MiniMax H3's bf16 transformer blocks from STUDIO_SPLIT_BLOCK on the second card (spark/gpus.py,
    # STUDIO_LAYER_SPLIT=on; off by default)
    NODE="$PREFIX/ComfyUI/custom_nodes/studio_split_h3"
    mkdir -p "$NODE"
    cp "$HERE/studio_split_h3/__init__.py" "$NODE/__init__.py.$$" && mv -f "$NODE/__init__.py.$$" "$NODE/__init__.py"
    # the Studio's MMAudio sampler (foley), beside kijai's ComfyUI-MMAudio when oneshot-setup-spark.sh --sound put it there
    NODE="$PREFIX/ComfyUI/custom_nodes/studio_mmaudio"
    if [ -f "$PREFIX/ComfyUI/custom_nodes/ComfyUI-MMAudio/nodes.py" ] && [ -f "$HERE/studio_mmaudio/__init__.py" ]; then
      mkdir -p "$NODE"
      cp "$HERE/studio_mmaudio/__init__.py" "$NODE/__init__.py.$$" && mv -f "$NODE/__init__.py.$$" "$NODE/__init__.py"
    fi
    cd "$PREFIX/ComfyUI"
    # --use-ck-attention: ComfyUI's own flash-attention kernel, 12% faster a pass than PyTorch's on a GB10
    if [ -n "${SPARK_GOVERNOR:-}" ]; then
      # shellcheck disable=SC2086
      exec python3 "$HERE/managed_comfy.py" run "$PREFIX/venv/bin/python" main.py --listen 127.0.0.1 --port "$PORT" ${SPARK_COMFY_ARGS:---use-ck-attention}
    fi
    # shellcheck disable=SC2086
    nice -n 19 "$PREFIX/venv/bin/python" main.py --listen 127.0.0.1 --port "$PORT" ${SPARK_COMFY_ARGS:---use-ck-attention} &
    pid=$!
    trap 'kill $pid 2>/dev/null' TERM INT
    # the guard stands beside the server until it ends (status 3: it stopped the server). It is started with the
    # system's python3 when the environment's own cannot run it, and if neither can, a plain loop holds the floor.
    GUARD=0
    for PY in "$PREFIX/venv/bin/python" python3; do
      RC=0; "$PY" "$HERE/memory_guard.py" watch --pid "$pid" || RC=$?
      if [ "$RC" = 0 ] || [ "$RC" = 3 ]; then GUARD=1; break; fi
    done
    if [ "$GUARD" = 0 ]; then
      FLOOR="${SPARK_MEMORY_FLOOR_MB:-12000}"
      echo "[tensorfold] memory floor: the guard could not start; holding ${FLOOR} MB with a plain loop"
      while kill -0 $pid 2>/dev/null; do
        read -r _ free _ < <(grep MemAvailable /proc/meminfo)
        if [ "$((free / 1024))" -lt "$FLOOR" ]; then echo "[tensorfold] memory floor: $((free / 1024)) MB available, under the floor of ${FLOOR} MB: stopping ComfyUI"; kill -9 $pid; fi
        sleep 0.2
      done
    fi
    wait $pid 2>/dev/null || true;;
  start)
    pgrep -f "$MATCH" >/dev/null && { echo "already running"; exit 0; }
    [ -x "$PREFIX/venv/bin/python" ] && [ -f "$PREFIX/ComfyUI/main.py" ] || { echo "run oneshot-setup-spark.sh first: nothing at $PREFIX" >&2; exit 1; }
    rm -f "$COMFY_KIND"
    nohup setsid bash "$0" run > "$COMFY_LOG" 2>&1 < /dev/null &
    echo "ComfyUI starting on 127.0.0.1:$PORT (log: $COMFY_LOG)";;
  *) echo "usage: comfy.sh start|stop|status" >&2; exit 2;;
esac
