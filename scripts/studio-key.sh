#!/bin/bash
# The studio's access key (app/access.py), for another device: a browser on this computer needs none. Prints the key
# and the address that signs a phone in (its Tailscale name, after `tailscale serve --bg 7870`); Settings → Access
# key shows the same with a QR code.
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
# the sign-in links the running studio offers (Settings → Access key shows them with QR codes): its network addresses
# when it is opened to the network (HOST=0.0.0.0), and its Tailscale name for a phone
LINKS="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/key/share" 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit()
for link in d.get("links", []):
    if link.get("kind"):
        print(f"{link[\"label\"] + \":\":34} {link[\"url\"]}")
if not d.get("on_network"):
    print("(This studio listens on this computer only: start it with HOST=0.0.0.0 to sign in another computer on your network.)")
' 2>/dev/null)"
if [ -n "$LINKS" ]; then echo "$LINKS"; else echo "(Start the studio to see its sign-in links.)"; fi
echo
echo "Open a link once on the other device: it stays signed in. Or type the key when the studio asks."
