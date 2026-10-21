#!/bin/bash
# The TensorFold Studio web app: create wizard, production board, clip editor.
#   bash scripts/app.sh                 # http://127.0.0.1:7870
#   HOST=0.0.0.0 bash scripts/app.sh    # also reachable from other machines on your network (behind the access key, app/access.py: trusted LANs only)
# Environment: PREFIX, QWEN_MODEL_DIR, H3_MODEL_DIR (as for the other scripts), HOST, PORT, STUDIO_HOME (default
# ~/ImaginationStudio, where projects, uploads and renders are kept).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
export PATH="/opt/homebrew/bin:$PATH"
# shellcheck disable=SC1091
. "$HERE/spark/gpu.sh"
studio_pin_gpu
[ -z "${STUDIO_GPU:-}" ] || echo "  compute GPU ${STUDIO_GPU}: $(studio_smi --query-gpu=name --format=csv,noheader | head -1)"
[ -x "$PREFIX/venv/bin/python" ] || { echo "run oneshot-setup.sh first: no environment at $PREFIX" >&2; exit 1; }
echo "Imagination Studio by keys+Wasserman · powered by TensorFold on http://${HOST:-127.0.0.1}:${PORT:-7870}"
# The app starts without ffmpeg and says what it then cannot do (long videos, the timeline export, frames taken from a
# clip, reference videos and audio); the page shows the same line.
for TOOL in ffmpeg ffprobe; do
  command -v "$TOOL" >/dev/null || echo "  ! $TOOL is not installed on this machine: $([ "$(uname -s)" = Linux ] && echo "sudo apt install ffmpeg" || echo "brew install ffmpeg")" >&2
done
# One launcher per checkout: it updates this git tree, so a second one is refused.
# A second studio on another GPU sets STUDIO_GPU and its own PORT and runs the server
# directly. A worker Spark still uses STUDIO_PIDFILE for the same bypass.
if [ -n "${STUDIO_PIDFILE:-}" ] || { [ -n "${STUDIO_GPU:-}" ] && [ "${PORT:-7870}" != 7870 ]; } || ! command -v git >/dev/null || ! git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exec "$PREFIX/venv/bin/python" "$HERE/app/server.py"
fi
exec "$PREFIX/venv/bin/python" "$HERE/app/launcher.py"
