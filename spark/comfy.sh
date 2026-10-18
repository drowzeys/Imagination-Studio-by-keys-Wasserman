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
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
    pkill -f "$MATCH" 2>/dev/null && echo "stopped" || echo "not running"; rm -f "$PREFIX/comfy.kind";;
  status) pgrep -f "$MATCH" >/dev/null && echo "running on 127.0.0.1:$PORT" || echo "not running";;
  run)
    export PATH="/usr/local/cuda/bin:$PATH" CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}" MAX_JOBS="${MAX_JOBS:-2}"
    # TensorFold's H3 engine as a ComfyUI node, when it has been built (spark/build-engine.sh)
    NODE="$PREFIX/ComfyUI/custom_nodes/tensorfold_h3"
    rm -rf "$NODE"
    if [ -s "$PREFIX/tf-h3/libtf_h3.so" ]; then
      mkdir -p "$NODE" && cp "$HERE/tensorfold_h3/__init__.py" "$NODE/"
      export TF_H3_LIB="$PREFIX/tf-h3/libtf_h3.so"
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
    rm -f "$PREFIX/comfy.kind"
    nohup setsid bash "$0" run > "$PREFIX/comfy.log" 2>&1 < /dev/null &
    echo "ComfyUI starting on 127.0.0.1:$PORT (log: $PREFIX/comfy.log)";;
  *) echo "usage: comfy.sh start|stop|status" >&2; exit 2;;
esac
