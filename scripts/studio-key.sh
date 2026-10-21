#!/bin/bash
# The studio's access key (app/access.py), and the addresses that open the page with it: on this computer, and on its
# Tailscale name for a phone (after `tailscale serve --bg 7870`). A browser opened once at one of them keeps the key.
#   bash scripts/studio-key.sh          # the key and the addresses
#   bash scripts/studio-key.sh --key    # the key alone, for a script
#   bash scripts/studio-key.sh --new    # a new key: every browser and phone signed in so far asks again
# The key is <studio folder>/access-key (mode 0600). The app makes it when it starts; it is kept across restarts and updates.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$(python3 "$HERE/app/studio_home.py" 2>/dev/null || echo "${STUDIO_HOME:-$HOME/ImaginationStudio}")"
FILE="$HOME_DIR/access-key"; PORT="${PORT:-7870}"
if [ "${1:-}" = --new ]; then
  rm -f "$FILE"
  echo "The old key is gone. Restart the studio (it makes the new one), then run this again." >&2
  exit 0
fi
if [ ! -s "$FILE" ]; then
  echo "No access key yet in $HOME_DIR: start the studio once (it makes one), then run this again." >&2
  exit 1
fi
KEY="$(tr -d '[:space:]' < "$FILE")"
if [ "${1:-}" = --key ]; then echo "$KEY"; exit 0; fi
echo "Access key: $KEY"
echo
echo "On this computer:  http://127.0.0.1:$PORT/?key=$KEY"
TS="$(command -v tailscale || { [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] && echo /Applications/Tailscale.app/Contents/MacOS/Tailscale; })"
if [ -n "$TS" ]; then
  NAME="$("$TS" status --json --peers=false 2>/dev/null | python3 -c 'import sys,json; print((json.load(sys.stdin).get("Self") or {}).get("DNSName","").rstrip("."))' 2>/dev/null)"
  [ -n "$NAME" ] && echo "On your phone:      https://$NAME/?key=$KEY   (after: tailscale serve --bg $PORT)"
fi
echo
echo "Open one once: the browser keeps the key. Or open the studio and type the key when it asks."
