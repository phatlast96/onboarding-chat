#!/usr/bin/env bash
set -euo pipefail
set -m

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  local pid
  for pid in $(jobs -p); do
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  done
  wait || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM

echo "API  http://127.0.0.1:8000"
(
  cd "$root/onboarding-backend"
  exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
) &

echo "App  http://localhost:3000"
(
  cd "$root/onboarding-frontend"
  exec npm run dev -- --port 3000
) &

wait
