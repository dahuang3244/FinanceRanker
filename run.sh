#!/usr/bin/env bash
# Start the FinanceRanker service (API + web UI).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

echo "Using interpreter: $($PYTHON -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if ! $PYTHON -c "import fastapi" 2>/dev/null; then
  echo "Dependencies missing. Create the venv first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

export PYTHONPATH="$PWD"
HOST="${FR_HOST:-127.0.0.1}"
PORT="${FR_PORT:-8848}"

echo "FinanceRanker -> http://${HOST}:${PORT}"
exec $PYTHON -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
