#!/usr/bin/env bash
# Start the whole CCIFJ Concierge stack: database migrations, the FastAPI
# backend, and the Vite frontend. Both servers listen on 0.0.0.0 so other
# devices on the network can reach them. Press Ctrl+C to stop everything.
#
# Optional overrides: API_PORT (default 8001), WEB_PORT (default 5173),
# NO_RELOAD=0 to enable auto-reload during development. Demo default is stable.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-8001}"
WEB_PORT="${WEB_PORT:-5173}"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null || fail "uv is not installed (https://docs.astral.sh/uv/)."
command -v npm >/dev/null || fail "npm is not installed."

# --- Environment -----------------------------------------------------------
[[ -f .env ]] || fail ".env is missing. Copy .env.example to .env and fill it in."
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"' || true; }

secret="$(env_value CCIFJ_AUTH_SECRET)"
if [[ -z "$secret" || "$secret" == "paste-the-generated-value-here" ]]; then
  fail "Set CCIFJ_AUTH_SECRET in .env. Generate one with:
  python -c \"import secrets; print(secrets.token_urlsafe(48))\""
fi

# --- Ollama (only when using local models) ---------------------------------
if [[ "$(env_value model_type)" == "qwen" ]]; then
  if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    command -v ollama >/dev/null || fail "model_type=qwen needs Ollama, which is not installed."
    info "Starting Ollama"
    ollama serve >/dev/null 2>&1 &
    for _ in {1..20}; do
      curl -fsS --max-time 1 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
      sleep 0.5
    done
  fi
fi

# --- Dependencies ----------------------------------------------------------
info "Syncing Python dependencies"
uv sync --frozen --quiet

if [[ ! -d ccifj-frontend/node_modules ]]; then
  info "Installing frontend dependencies"
  (cd ccifj-frontend && npm ci)
fi

# --- Database --------------------------------------------------------------
info "Applying database migrations"
uv run concierge-db upgrade

# --- Servers ---------------------------------------------------------------
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
port_busy "$API_PORT" && fail "Port $API_PORT is already in use (another API running?). Stop it or set API_PORT."
port_busy "$WEB_PORT" && fail "Port $WEB_PORT is already in use (another frontend running?). Stop it or set WEB_PORT."

# On exit, stop every process this script started (the whole process group).
trap 'trap - INT TERM EXIT; echo; info "Stopping"; kill 0 2>/dev/null; wait' INT TERM EXIT

reload_flag=()
[[ "${NO_RELOAD:-1}" == "0" ]] && reload_flag=(--reload)

info "Starting API on 0.0.0.0:$API_PORT"
uv run uvicorn concierge.main:app --app-dir src --host 0.0.0.0 --port "$API_PORT" "${reload_flag[@]}" &

info "Starting frontend on 0.0.0.0:$WEB_PORT"
(cd ccifj-frontend && CCIFJ_API_TARGET="http://127.0.0.1:$API_PORT" \
  npm run dev -- --host 0.0.0.0 --port "$WEB_PORT") &

sleep 2
echo
info "Open the app at:"
echo "    http://localhost:$WEB_PORT"
for ip in $(hostname -I 2>/dev/null || ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1); do
  [[ "$ip" == *:* ]] && continue
  echo "    http://$ip:$WEB_PORT"
done
echo "    (API docs: http://localhost:$API_PORT/docs)"
echo

# If either server exits, stop the other one too.
wait -n
