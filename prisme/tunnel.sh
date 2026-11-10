#!/usr/bin/env bash
# Give the concierge API a public address, so the Prisme.ai agent can reach it
# while the backend runs on a laptop (Cloudflare quick tunnel, no account needed).
#
#   prisme/tunnel.sh         open the tunnel, or show the one already open, and
#                            print the Endpoint URL for the agent's function
#   prisme/tunnel.sh stop    close it
#
# The tunnel runs in the background: the address stays the same until it is
# stopped or the machine sleeps or restarts. Every new start gives a new
# address, which then has to be pasted into the Prisme function again.
# While it is open, anyone who has the address can use the concierge.
#
# Optional: API_PORT (default 8001, the port start.sh uses).
set -euo pipefail

PORT="${API_PORT:-8001}"
# Kept outside the project, so every copy of this script sees the same tunnel.
STATE="${TMPDIR:-/tmp}/ccifj-prisme-tunnel-$(id -u)-$PORT"
LOG="$STATE.log"
PIDFILE="$STATE.pid"

running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
address() { grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -n1 || true; }
answers() { curl -fsS --max-time 8 "$1/health" >/dev/null 2>&1; }

if [[ "${1:-}" == "stop" ]]; then
  if running; then kill "$(cat "$PIDFILE")"; echo "Tunnel closed."; else echo "No tunnel is open."; fi
  rm -f "$PIDFILE"
  exit 0
fi

command -v cloudflared >/dev/null || { echo "cloudflared is not installed: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2; exit 1; }
answers "http://127.0.0.1:$PORT" || { echo "The concierge API is not answering on port $PORT. Start it first with ./start.sh." >&2; exit 1; }

if running && [[ -n "$(address)" ]] && ! answers "$(address)"; then
  # The process is alive but its address is dead (the machine slept or changed network).
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "The previous address stopped working; opening a new one."
fi

if ! running; then
  : > "$LOG"
  nohup cloudflared tunnel --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in {1..60}; do
    [[ -n "$(address)" ]] && break
    running || { echo "cloudflared stopped. See $LOG" >&2; exit 1; }
    sleep 0.5
  done
  sleep 5   # asking for a brand-new name too early makes the local resolver remember "not found"
fi

url="$(address)"
[[ -n "$url" ]] || { echo "No address yet. See $LOG" >&2; exit 1; }

# A new address needs a few seconds before it answers from the internet.
reachable=no
for _ in {1..20}; do
  if answers "$url"; then reachable=yes; break; fi
  sleep 2
done

echo
echo "Endpoint URL for the Prisme function:"
echo "    $url/api/v1/chat"
echo
if [[ "$reachable" == yes ]]; then
  echo "The address answers from the internet."
else
  echo "The address does not answer yet. Wait a minute and run this again to check."
fi
echo "In Prisme: Capabilities -> Ask the CCIFJ Concierge -> Endpoint URL -> Update,"
echo "then ... -> Publish Changes. Close the tunnel with: prisme/tunnel.sh stop"
