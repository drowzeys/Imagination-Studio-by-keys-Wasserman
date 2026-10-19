#!/bin/bash
# The essential LoRA bundle for the Keys and Wasserman LoRA Hub, in one command, on a Mac or a DGX Spark.
# It fetches every preset of the hub from its author's own page into one folder laid out by the hub's six groups
# (01-camera-viewpoint ... 06-support-finishing, with bundle.json as the menu), checks each file's sha256, keeps the
# author's model card and licence beside it, and installs the set into the hub. After one run the Studio needs none of
# those links again; copy the folder to another machine or drive and install from it with no network:
#   bash oneshot-essential-bundle.sh                                  # everything (about 4 GB), then install into the hub
#   bash oneshot-essential-bundle.sh --categories camera,style        # some groups: wasserman camera transform character motion style finish fun
#   bash oneshot-essential-bundle.sh --only camera-motion,anime-slider
#   bash oneshot-essential-bundle.sh --dest /Volumes/Drive/lora-bundle   # where the bundle is kept (default ~/ImaginationStudio/lora-bundle)
#   bash oneshot-essential-bundle.sh --source /Volumes/Drive/lora-bundle # take the files from another copy of a bundle (or LORA_BUNDLE=...)
#   bash oneshot-essential-bundle.sh --verify --json                  # check what is there, fetch nothing, one JSON summary
#   bash oneshot-essential-bundle.sh --dry-run                        # what would be fetched, and its size
# No prompts, safe to run unattended or by an agent, and safe to run twice (the second run fetches nothing).
# Exit 0: all present. 1: some unavailable (listed; the rest are installed). 2: usage, or not enough disk.
# --no-install keeps the files in the bundle without installing them into the hub.
# Each LoRA is its author's work under its own licence; the pages are named in the hub and in bundle.json, for credit.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
INSTALL=1; ARGS=(); DEST=""; CHECK=0; GROUPS_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-install) INSTALL=0;;
    --dest) DEST="$2"; ARGS+=("$1" "$2"); shift;; --dest=*) DEST="${1#--dest=}"; ARGS+=("$1");;
    --categories) GROUPS_ARG="$2"; ARGS+=("$1" "$2"); shift;; --categories=*) GROUPS_ARG="${1#--categories=}"; ARGS+=("$1");;
    --verify|--dry-run) CHECK=1; ARGS+=("$1");;
    *) ARGS+=("$1");;
  esac; shift
done
# The essential package: one archive per platform with the LoRAs whose stated licences allow passing them on (14 of the
# 22), so a first run takes one download and does not depend on fourteen authors' pages. The rest come from their authors.
# LORA_PACKAGE=none goes to the authors' pages for everything; LORA_PACKAGE=<file or address> names another package.
PKG_BASE="https://huggingface.co/drowzeys/keys-studio-lora-essential/resolve/main"
STUDIO_DIR="${STUDIO_HOME:-$(python3 "$HERE/app/studio_home.py")}"  # app/studio_home.py: the folder the app uses
PKG_FILE="keys-studio-lora-essential-$([ "$(uname -s)" = Darwin ] && echo mac || echo spark)-2026-10-13-r3.tar"
case " ${ARGS[*]-} " in *" --package"*|*" --source"*|*" --make-package"*|*" --verify "*|*" --dry-run "*) ;; *)
  if [ "${LORA_PACKAGE:-}" != none ] && [ -z "${LORA_BUNDLE:-}" ] && [ ! -e "${DEST:-$STUDIO_DIR/lora-bundle}/package.json" ]; then
    ARGS+=(--package "${LORA_PACKAGE:-$PKG_BASE/$PKG_FILE}")
  fi;; esac
PY="$PREFIX/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)" || { echo "python3 is needed" >&2; exit 2; }
"$PY" "$HERE/scripts/lora-bundle.py" ${ARGS[@]+"${ARGS[@]}"}; RC=$?
[ $RC -le 1 ] || exit $RC
if [ "$INSTALL" = 1 ] && [ "$CHECK" = 0 ]; then
  DEST="${DEST:-$STUDIO_DIR/lora-bundle}"
  if [ -x "$PREFIX/venv/bin/python" ]; then
    # into the hub from the bundle only: nothing here goes to the network
    LORA_BUNDLE="$DEST" LORA_OFFLINE=1 H3_MODEL_DIR="${H3_MODEL_DIR:-$HOME/h3-models/MiniMax-H3}" "$PREFIX/venv/bin/python" "$HERE/app/loras.py" install "${GROUPS_ARG:-all}" >&2 || true
  else
    echo "the Studio is not installed at $PREFIX yet: the bundle is ready at $DEST, and the hub installs from it at its first start" >&2
  fi
fi
exit $RC
