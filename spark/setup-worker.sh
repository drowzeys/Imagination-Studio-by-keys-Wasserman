#!/bin/bash
# Make another DGX Spark a render node of this studio. Run on the Spark that has the studio installed (the head):
#   bash spark/setup-worker.sh 10.0.0.5            # set the node up, start it, and add it to this studio's node list
#   bash spark/setup-worker.sh 10.0.0.5 stop       # stop the node's studio and ComfyUI
#   bash spark/setup-worker.sh 10.0.0.5 remove     # stop it and unmount the models (its install stays)
# What it does: the node gets a copy of this install ($PREFIX: Python, ComfyUI and the engine, about 20 GB) and of
# this pack, and reads the models from this machine over NFS, read-only, at the same paths (no weights are copied:
# the node needs about 25 GB of disk). It runs the studio with no page open, on HEAD-facing address NODE:PORT, with
# a node token; this studio then hands it queued jobs (app/nodes.py). Needs: key-based ssh to the node as the same
# user, passwordless sudo on both machines (an NFS export here, a mount and ffmpeg there), the same home path.
# Environment: PREFIX, PORT (7870), HEAD (this machine's address as the node sees it; default: the address on the
# node's subnet), MODELS (folders to share, default: the ones spark-studio-models points into), STUDIO_URL (this
# studio, default http://127.0.0.1:7870).
set -euo pipefail
NODE="${1:?the node's address}"; DO="${2:-up}"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"; PORT="${PORT:-7870}"
PACK="$(cd "$(dirname "$0")/.." && pwd)"; STUDIO_URL="${STUDIO_URL:-http://127.0.0.1:7870}"
rsh() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$NODE" "$@"; }
HEAD="${HEAD:-$(ip -4 route get "$NODE" | sed -n 's/.* src \([0-9.]*\).*/\1/p')}"
if [ "$DO" = stop ] || [ "$DO" = remove ]; then
  rsh "kill \$(cat ~/studio-node/server.pid 2>/dev/null) 2>/dev/null; PREFIX=$PREFIX bash ~/studio-node/pack/spark/comfy.sh stop; true"
  [ "$DO" = remove ] && rsh "for m in \$(cat ~/studio-node/mounts 2>/dev/null); do sudo -n umount \"\$m\" || true; done"
  echo "node $NODE: $DO done"; exit 0
fi
[ -x "$PREFIX/venv/bin/python" ] || { echo "no studio install at $PREFIX on this machine" >&2; exit 1; }
# the model folders: spark-studio-models and every folder its links point into
MODELS="${MODELS:-$( { echo "$HOME/spark-studio-models"; find "$HOME/spark-studio-models" -type l -printf '%l\n' | grep -E '/models(/|$)' | sed -E 's|(/models)/.*|\1|' ; } | sort -u | tr '\n' ' ')}"
echo "[1/5] sharing the models read-only with $NODE: $MODELS"
net="$(ip -4 -o addr show | awk -v ip="$HEAD" '$4 ~ "^"ip"/" {print $4}' | head -1)"; net="$(python3 -c "import ipaddress,sys;print(ipaddress.ip_interface(sys.argv[1]).network)" "$net")"
for m in $MODELS; do
  grep -qsE "^$m[[:space:]]" /etc/exports || echo "$m $net(ro,no_subtree_check,crossmnt)" | sudo -n tee -a /etc/exports >/dev/null
done
sudo -n exportfs -ra
echo "[2/5] the node's tools"
rsh "command -v ffmpeg >/dev/null || { sudo -n apt-get update -qq && sudo -n apt-get install -y -qq ffmpeg >/dev/null; }; dpkg -s nfs-common >/dev/null 2>&1 || sudo -n apt-get install -y -qq nfs-common >/dev/null; ffmpeg -version | head -1"
echo "[3/5] the install and the pack ($(du -sh "$PREFIX" | cut -f1))"
rsh "mkdir -p $PREFIX ~/studio-node/pack ~/TensorFoldStudio"
rsync -a --delete --exclude comfy.log --exclude app.log --exclude 'ComfyUI/output/*' --exclude 'ComfyUI/input/*' --exclude 'ComfyUI/temp/*' "$PREFIX/" "$NODE:$PREFIX/"
rsync -a --delete --exclude .git --exclude samples --exclude '__pycache__' "$PACK/" "$NODE:studio-node/pack/"
echo "[4/5] the models, mounted at the same paths"
rsh "set -e; : > ~/studio-node/mounts; for m in $MODELS; do mkdir -p \$m; mountpoint -q \$m || sudo -n mount -t nfs -o ro,nconnect=8,rsize=1048576 $HEAD:\$m \$m; echo \$m >> ~/studio-node/mounts; done; ls $HOME/spark-studio-models | head -3"
echo "[5/5] the node's studio on $NODE:$PORT"
TOKEN="$(rsh "[ -s ~/TensorFoldStudio/node-token ] || { python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > ~/TensorFoldStudio/node-token; chmod 600 ~/TensorFoldStudio/node-token; }; cat ~/TensorFoldStudio/node-token")"
rsh "kill \$(cat ~/studio-node/server.pid 2>/dev/null) 2>/dev/null; sleep 1; cd ~/studio-node/pack && PREFIX=$PREFIX HOST=0.0.0.0 PORT=$PORT SPARK_MEMORY_FLOOR_MB=\${SPARK_MEMORY_FLOOR_MB:-6000} nohup setsid $PREFIX/venv/bin/python app/server.py > ~/studio-node/server.log 2>&1 < /dev/null & echo \$! > ~/studio-node/server.pid"
for _ in $(seq 40); do curl -s -m 3 -H "X-Studio-Token: $TOKEN" "http://$NODE:$PORT/api/node/info" | grep -q '"version"' && break; sleep 1; done
curl -s -m 5 -H "X-Studio-Token: $TOKEN" "http://$NODE:$PORT/api/node/info" | head -c 400; echo
if curl -s -m 3 "$STUDIO_URL/api/nodes" | grep -q '"nodes"'; then
  curl -s -m 20 -X POST "$STUDIO_URL/api/nodes" -H 'Content-Type: application/json' -d "{\"url\": \"http://$NODE:$PORT\", \"token\": \"$TOKEN\"}" | head -c 300; echo
else
  echo "this studio ($STUDIO_URL) does not have render nodes yet: add http://$NODE:$PORT with the token in ~/TensorFoldStudio/node-token on the node"
fi
