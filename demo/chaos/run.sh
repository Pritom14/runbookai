#!/bin/bash
set -e

echo 'Starting RunbookAI server (if not running)...'
# Check if server is running; if not, start it in background
if ! curl -s http://localhost:7000 > /dev/null 2>&1; then
  nohup env SUGGEST_MODE=false .venv/bin/uvicorn runbookai.main:app --port 7000 > /tmp/runbookai.log 2>&1 &
  echo 'Server started in background'
  for i in {1..30}; do
    if curl -s http://localhost:7000/health > /dev/null 2>&1; then
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo 'RunbookAI server did not become healthy. See /tmp/runbookai.log'
      exit 1
    fi
    sleep 1
  done
else
  echo 'Server already running on :7000. Ensure SUGGEST_MODE=false for autonomous demo.'
fi

echo 'Setting up demo...'
bash demo/chaos/setup.sh

echo 'Opening browser...'
open http://localhost:7000/static/chaos-board.html 2>/dev/null || xdg-open http://localhost:7000/static/chaos-board.html 2>/dev/null || echo 'Open http://localhost:7000/static/chaos-board.html in your browser'

echo 'Starting chaos demo...'
SEQUENCE="${1:-Hardware temp,Process kill,Disk fill}"
DELAY="${2:-8}"
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python demo/chaos/chaos.py --sequence "$SEQUENCE" --delay "$DELAY"
else
  python3 demo/chaos/chaos.py --sequence "$SEQUENCE" --delay "$DELAY"
fi

echo 'Demo complete. Cleaning up...'
bash demo/chaos/teardown.sh

echo 'Done.'
