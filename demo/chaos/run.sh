#!/bin/bash
set -e

echo 'Setting up demo...'
bash demo/chaos/setup.sh

echo 'Starting RunbookAI server (if not running)...'
# Check if server is running; if not, start it in background
if ! curl -s http://localhost:7000 > /dev/null 2>&1; then
  .venv/bin/uvicorn runbookai.main:app --port 7000 > /tmp/runbookai.log 2>&1 &
  echo 'Server started in background'
  sleep 5
fi

echo 'Opening browser...'
open http://localhost:7000/incidents 2>/dev/null || xdg-open http://localhost:7000/incidents 2>/dev/null || echo 'Open http://localhost:7000/incidents in your browser'

echo 'Starting chaos demo...'
python demo/chaos/chaos.py

echo 'Demo complete. Cleaning up...'
bash demo/chaos/teardown.sh

echo 'Done.'
