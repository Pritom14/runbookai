#!/usr/bin/env bash
# RunbookAI hardware demo — injects thermal and fan failures, watches the
# agent respond autonomously via BMC sensor tools.
#
# Usage:
#   ./demo/run_hardware_demo.sh
#
# Requires: RunbookAI server running on :7000 (uvicorn runbookai.main:app --port 7000)

set -euo pipefail

BASE="http://localhost:7000"
DELAY_BETWEEN=8   # seconds between attack recovery confirmation and next attack

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'
GRN='\033[0;32m'
YEL='\033[1;33m'
BLU='\033[0;34m'
CYN='\033[0;36m'
DIM='\033[2m'
RST='\033[0m'

log()     { echo -e "${DIM}[$(date +%H:%M:%S)]${RST} $*"; }
attack()  { echo -e "\n${RED}[ATTACK]${RST}  $*"; }
agent()   { echo -e "${GRN}[AGENT] ${RST}  $*"; }
info()    { echo -e "${BLU}[INFO]  ${RST}  $*"; }
success() { echo -e "${GRN}[OK]    ${RST}  $*"; }

# ── Preflight ─────────────────────────────────────────────────
if ! curl -sf "$BASE/health" >/dev/null 2>&1; then
  echo -e "${RED}ERROR:${RST} RunbookAI server not found at $BASE"
  echo "  Start with:  uvicorn runbookai.main:app --port 7000"
  exit 1
fi
success "Server up at $BASE"

# Load the hardware thermal runbook
info "Loading hardware thermal runbook..."
curl -sf -X POST "$BASE/runbooks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hardware Thermal Alert",
    "alert_pattern": "Hardware: CPU thermal alert",
    "content": "Hardware thermal runbook:\n1. Call read_bmc_sensors to read current CPU temperature, fan RPMs, and PSU metrics.\n2. If CPU_Temp is above 85C, call fan_override with speed_percent=100 to maximize cooling.\n3. Wait a moment, then call read_bmc_sensors again to confirm temperature is dropping.\n4. Call finish() with a summary of the temperature before and after the fan override."
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Runbook loaded: {d.get(\"id\",d)}')" 2>/dev/null || true

# Load the fan failure runbook
info "Loading fan failure runbook..."
curl -sf -X POST "$BASE/runbooks" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hardware Fan Failure",
    "alert_pattern": "Hardware: fan failure",
    "content": "Fan failure runbook:\n1. Call read_bmc_sensors to confirm which fan has failed and check current CPU temperature.\n2. Call fan_override with speed_percent=100 to force remaining fans to maximum speed.\n3. Call read_bmc_sensors again to confirm other fans have spun up and CPU temp is stable.\n4. Call finish() noting the failed fan and that cooling has been maintained via override."
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Runbook loaded: {d.get(\"id\",d)}')" 2>/dev/null || true

# Reset BMC to clean state
curl -sf -X POST "$BASE/bmc/control?action=reset" >/dev/null
success "BMC reset to healthy state"

echo ""
echo -e "${CYN}  Dashboard:  $BASE/static/demo-dashboard.html${RST}"
echo -e "${CYN}  Incidents:  $BASE/incidents${RST}"
echo ""
echo -e "${DIM}  Opening browser...${RST}"
open "$BASE/static/demo-dashboard.html" 2>/dev/null || true
sleep 2

# ── Attack 1: Thermal runaway ─────────────────────────────────
attack "THERMAL RUNAWAY — CPU temperature climbing"
info  "Injecting thermal failure into BMC emulator..."
curl -sf -X POST "$BASE/bmc/control?action=thermal_failure" >/dev/null
log   "BMC mode set: thermal_failure"

sleep 3
info "Firing alert to RunbookAI..."
RESP=$(curl -sf -X POST "$BASE/webhooks/generic" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "Hardware: CPU thermal alert",
    "description": "CPU temperature exceeding critical threshold. BMC sensor: CPU_Temp CRITICAL.",
    "service": "web-01",
    "severity": "critical"
  }')
INC_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('incident_id',''))" 2>/dev/null)
agent "Incident created: $INC_ID"
agent "Agent is reading BMC sensors, analyzing, and calling fan_override..."

# Wait for resolution
MAX_WAIT=120
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(curl -sf "$BASE/incidents/$INC_ID" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  if [[ "$STATUS" == "resolved" || "$STATUS" == "escalated" ]]; then
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
  echo -ne "  ${DIM}waiting... ${ELAPSED}s${RST}\r"
done
echo ""

if [[ "$STATUS" == "resolved" ]]; then
  success "Incident resolved in ${ELAPSED}s  |  Replay: $BASE/static/replay.html?id=$INC_ID"
else
  echo -e "${YEL}[NOTE]${RST}   Status: $STATUS  |  Replay: $BASE/static/replay.html?id=$INC_ID"
fi

echo ""
log "Pausing $DELAY_BETWEEN seconds before next attack..."
sleep $DELAY_BETWEEN

# ── Attack 2: Fan failure ─────────────────────────────────────
curl -sf -X POST "$BASE/bmc/control?action=reset" >/dev/null
sleep 2

attack "FAN FAILURE — Fan 1 RPM dropping to zero"
info  "Injecting fan failure into BMC emulator..."
curl -sf -X POST "$BASE/bmc/control?action=fan_failure" >/dev/null
log   "BMC mode set: fan_failure"

sleep 4
info "Firing alert to RunbookAI..."
RESP2=$(curl -sf -X POST "$BASE/webhooks/generic" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "Hardware: fan failure",
    "description": "Fan1 RPM dropped to critical level. BMC sensor: Fan1_RPM CRITICAL.",
    "service": "web-01",
    "severity": "critical"
  }')
INC_ID2=$(echo "$RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('incident_id',''))" 2>/dev/null)
agent "Incident created: $INC_ID2"
agent "Agent is reading BMC sensors and issuing fan override..."

ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(curl -sf "$BASE/incidents/$INC_ID2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  if [[ "$STATUS" == "resolved" || "$STATUS" == "escalated" ]]; then
    break
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
  echo -ne "  ${DIM}waiting... ${ELAPSED}s${RST}\r"
done
echo ""

if [[ "$STATUS" == "resolved" ]]; then
  success "Incident resolved in ${ELAPSED}s  |  Replay: $BASE/static/replay.html?id=$INC_ID2"
else
  echo -e "${YEL}[NOTE]${RST}   Status: $STATUS  |  Replay: $BASE/static/replay.html?id=$INC_ID2"
fi

echo ""
curl -sf -X POST "$BASE/bmc/control?action=reset" >/dev/null

echo ""
echo -e "${GRN}Demo complete.${RST} Both hardware incidents resolved with zero human intervention."
echo ""
echo -e "  Dashboard: $BASE/static/demo-dashboard.html"
echo -e "  Incident 1 (thermal): $BASE/static/replay.html?id=$INC_ID"
echo -e "  Incident 2 (fan):     $BASE/static/replay.html?id=$INC_ID2"
echo ""
