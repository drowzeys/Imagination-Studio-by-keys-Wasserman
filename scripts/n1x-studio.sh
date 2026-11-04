#!/bin/bash
# The Studio on an NVIDIA RTX Spark N1X, inside its WSL2 Ubuntu: what the Windows launcher (Start-Studio.cmd, made by
# oneshot-setup-rtx-spark-n1x.ps1) runs. STUDIO_N1X=1 makes the memory guard take the GPU's carve-out as the pool and
# flag, rather than refuse, a job its estimate says may not fit (spark/memory_guard.py). The Studio stays up as long as
# the launcher's window is open: WSL shuts Ubuntu down shortly after its last Windows window closes.
#   bash scripts/n1x-studio.sh          start (the app's log also goes to ~/ImaginationStudio/app.log)
#   bash scripts/n1x-studio.sh stop     stop the Studio and its ComfyUI, giving the GPU's memory back
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export STUDIO_N1X=1
export PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
mkdir -p "$HOME/ImaginationStudio"
if [ "${1:-}" = stop ]; then
  # [a]pp: the pattern does not match this script's own command line
  pkill -f "[a]pp/launcher.py" 2>/dev/null
  pkill -f "[a]pp/server.py" 2>/dev/null
  for _ in $(seq 1 15); do pgrep -f "[a]pp/server.py" >/dev/null || break; sleep 1; done
  bash "$HERE/spark/comfy.sh" stop
  echo "Imagination Studio stopped"
  exit 0
fi
if pgrep -f "[a]pp/server.py" >/dev/null; then
  echo "Imagination Studio is already running on http://127.0.0.1:${PORT:-7870}"
  exit 0
fi
cd "$HERE"
bash scripts/app.sh 2>&1 | tee -a "$HOME/ImaginationStudio/app.log"
