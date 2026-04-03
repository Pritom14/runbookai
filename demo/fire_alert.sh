#!/bin/bash
# Fire a demo alert at the running RunbookAI instance.
#
# Usage:
#   ./demo/fire_alert.sh checkout   # complex: DB connection leak → escalate
#   ./demo/fire_alert.sh payment    # mundane: OOM crash → restart → recover

set -e

BASE="${RUNBOOKAI_URL:-http://localhost:7000}"
SCENARIO="${1:-checkout}"

case "$SCENARIO" in
  checkout|latency)
    PAYLOAD='{
      "alert_name": "checkout service p99 latency high",
      "severity": "high",
      "service": "checkout-service",
      "details": "p99 latency > 4s for the last 10 minutes. 503 error rate 12%.",
      "host": "ssh-target"
    }'
    echo "Firing: checkout-latency scenario (DB connection leak)"
    ;;
  payment|503)
    PAYLOAD='{
      "alert_name": "payment-service 503 rate spike",
      "severity": "critical",
      "service": "payment-service",
      "details": "503 error rate at 47% for 5 minutes. On-call paged.",
      "host": "ssh-target"
    }'
    echo "Firing: payment-service-503 scenario (OOM crash)"
    ;;
  disk)
    PAYLOAD='{
      "alert_name": "disk usage critical on web-01",
      "severity": "high",
      "service": "web-01",
      "details": "/var/log at 94% capacity. Write errors imminent.",
      "host": "ssh-target"
    }'
    echo "Firing: disk-full scenario"
    ;;
  *)
    echo "Unknown scenario: $SCENARIO"
    echo "Usage: $0 [checkout|payment|disk]"
    exit 1
    ;;
esac

RESPONSE=$(curl -s -X POST "$BASE/webhooks/generic" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

INCIDENT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['incident_id'])" 2>/dev/null)

echo "Incident created: $INCIDENT_ID"
echo ""
echo "Watch the agent work:"
echo "  Replay UI:  $BASE/incidents/$INCIDENT_ID/replay/ui"
echo "  Raw trace:  $BASE/incidents/$INCIDENT_ID/replay"
echo ""
echo "Approve pending actions (Suggest Mode):"
echo "  curl -X POST $BASE/approvals/<approval_id>/approve"
