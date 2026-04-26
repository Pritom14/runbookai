#!/bin/bash
set -e

BASE_URL="http://localhost:7000"

# Step 1: Check server is running
if ! curl -s "$BASE_URL/incidents" > /dev/null; then
    echo "Server is not running on port 7000. Start it with: uvicorn runbookai.main:app --port 7000"
    exit 1
fi
echo "Server running."

# Step 2: Load the hardware runbook
echo "Loading hardware runbook..."
curl -s -X POST "$BASE_URL/runbooks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "High CPU Temperature",
    "alert_pattern": "CPU Temperature",
    "content": "1. check_processes to find CPU-heavy processes.\n2. check_logs for OOM or runaway loops.\n3. restart_service if a known service is misbehaving.\n4. http_check to verify recovery.\n5. call finish() with summary."
  }' | jq .

# Step 3: Fire hardware alert
echo ""
echo "Firing hardware alert..."
alert_response=$(curl -s -X POST "$BASE_URL/webhooks/hardware" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "High CPU Temperature",
    "description": "CPU temp at 87C, threshold 80C",
    "service": "web-01",
    "severity": "critical",
    "sensor": "CPU_Temp",
    "value": "87",
    "unit": "C"
  }')

echo "$alert_response" | jq .

# Step 4: Print replay URL
incident_id=$(echo "$alert_response" | jq -r '.incident_id')
echo ""
echo "Incident ID: $incident_id"
echo "Replay UI:   $BASE_URL/incidents/$incident_id/replay/ui"
echo "Postmortem:  $BASE_URL/incidents/$incident_id/postmortem"
