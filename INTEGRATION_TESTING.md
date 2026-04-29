# Phase 2: Real Integrations Testing Guide

This document describes how to manually test each integration for Phase 2 of RunbookAI.

## Setup

1. Ensure RunbookAI is running:
   ```bash
   python -m runbookai.main
   # Or: python -c "from runbookai.main import start; start()"
   ```

2. Configure `.env` with your API keys (optional — each integration is optional):
   ```bash
   PAGERDUTY_API_KEY=your-pagerduty-key
   PAGERDUTY_WEBHOOK_SECRET=your-webhook-secret
   DATADOG_API_KEY=your-datadog-key
   DATADOG_SITE=datadoghq.com
   GRAFANA_API_KEY=your-grafana-key
   GRAFANA_BASE_URL=https://grafana.example.com
   GRAFANA_WEBHOOK_SECRET=your-webhook-secret
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
   ```

## Step 1: PagerDuty Integration

### Test Webhook Receipt (No API Key Required)

```bash
# 1. Create a test PagerDuty webhook payload
curl -X POST http://localhost:7000/webhooks/pagerduty \
  -H "Content-Type: application/json" \
  -H "X-PagerDuty-Signature: v1=test" \
  -d '{
    "event": {
      "event_type": "incident.triggered",
      "data": {
        "incident": {
          "id": "Q0RVJQLZWHSEKV",
          "title": "CPU Usage Critical",
          "service": {
            "id": "P123456",
            "name": "Production API"
          },
          "urgency": "high",
          "description": "CPU > 90%"
        }
      }
    }
  }'

# 2. Check response
# Expected: {"status": "accepted", "incident_id": "...", "pagerduty_incident_id": "Q0RVJQLZWHSEKV"}

# 3. Get incident details
curl http://localhost:7000/incidents/<incident_id>
```

### Test PagerDuty Resolution (Requires API Key)

If `PAGERDUTY_API_KEY` is set, the incident resolution will be written back to PagerDuty:

```bash
# Check logs for writeback success:
# "PagerDuty API success: incident Q0RVJQLZWHSEKV resolved"
# or error details if API key is invalid
```

**Verification:**
- Check RunbookAI logs for: "PagerDuty API call: PUT" and success/error messages
- The incident in PagerDuty should be marked as "resolved"

---

## Step 2: Datadog Integration

### Test Webhook Receipt

```bash
# 1. Send a Datadog alert webhook
curl -X POST http://localhost:7000/webhooks/datadog \
  -H "Content-Type: application/json" \
  -d '{
    "id": "12345678",
    "alert_title": "High Memory Usage",
    "alert_status": "alert",
    "trigger": {
      "metric": "system.mem.pct_used",
      "value": 85.5
    },
    "last_updated": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }'

# 2. Check response
# Expected: {"status": "accepted", "incident_id": "...", "datadog_monitor_id": "12345678"}

# 3. Get incident details
curl http://localhost:7000/incidents/<incident_id>
```

### Test Datadog Event Posting (Requires API Key)

If `DATADOG_API_KEY` and `DATADOG_SITE` are set, resolution will post to Datadog Events API:

```bash
# Check logs for writeback success:
# "Datadog API success: event posted"
# or error details if credentials are invalid
```

**Verification:**
- Check RunbookAI logs for: "Datadog API call: POST" and success/error messages
- Check Datadog Events for a new event with tag "runbookai:incident_id:..."

---

## Step 3: Grafana Integration

### Test Webhook Receipt with Signature Verification

```bash
# 1. Create test payload
PAYLOAD='{
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighCPU",
        "__alert_uid__": "abc123xyz789"
      },
      "annotations": {
        "description": "CPU usage > 80%"
      }
    }
  ]
}'

# 2. Generate HMAC signature (if secret is set)
# Signature = sha256=HMAC-SHA256(secret, payload)
# For testing without signature verification, send empty header or no secret

# 3. Send webhook
curl -X POST http://localhost:7000/webhooks/grafana \
  -H "Content-Type: application/json" \
  -H "X-Grafana-Signature: sha256=test" \
  -d "$PAYLOAD"

# 4. Check response
# Expected: {"status": "accepted", "incident_id": "...", "grafana_alert_uid": "abc123xyz789"}
```

### Test Grafana Alert Closure (Requires API Key)

If `GRAFANA_API_KEY` and `GRAFANA_BASE_URL` are set, resolution will post to Annotations API:

```bash
# Check logs for writeback success:
# "Grafana API success: alert logged"
# or error details if credentials are invalid
```

**Verification:**
- Check RunbookAI logs for: "Grafana API call: POST" and success/error messages
- Check Grafana Annotations for a new annotation from "runbookai"

---

## Step 4: Slack Integration

### Test Slack Connectivity

```bash
# 1. Test webhook connectivity
curl -X POST http://localhost:7000/webhooks/slack/test

# Expected response:
# {
#   "success": true,
#   "status": "ok",
#   "message": "Slack webhook is accessible and working"
# }

# Or if not configured:
# {
#   "success": true,
#   "status": "skipped",
#   "message": "Slack webhook not configured"
# }
```

### See Slack Notifications

When any incident is triggered or resolved:

```bash
# Trigger an incident (use any webhook above)
# Slack will receive messages for:
# - incident_started (when agent begins)
# - incident_resolved (when agent finishes)
# - approval_needed (if high-risk action)
# - incident_escalated (if cannot resolve)
```

**Verification:**
- Check your Slack channel for formatted incident messages
- Check RunbookAI logs for: "Slack API success: notification sent"

---

## Full End-to-End Test

This tests all integrations with a single incident:

```bash
# 1. Trigger a generic incident (creates incident in RunbookAI)
curl -X POST http://localhost:7000/webhooks/generic \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "Integration Test Alert",
    "description": "Testing Phase 2 integrations",
    "service": "test-service",
    "severity": "critical"
  }'

# 2. Monitor logs for:
# - "incident_started" Slack notification
# - Agent running and proposing actions
# - Any write-backs to configured integrations

# 3. Check incident detail
curl http://localhost:7000/incidents/<incident_id>

# 4. When agent resolves incident, verify:
# - "incident_resolved" Slack notification
# - "PagerDuty API call: PUT" (if PAGERDUTY_API_KEY set)
# - "Datadog API call: POST" (if DATADOG_API_KEY set)
# - "Grafana API call: POST" (if GRAFANA_API_KEY set)
```

---

## Troubleshooting

### PagerDuty
- **Signature verification failed**: Check `PAGERDUTY_WEBHOOK_SECRET` matches webhook config
- **Incident not created**: Check logs for "Ignoring PagerDuty event type"
- **Resolution not written back**: Verify `PAGERDUTY_API_KEY` is set and valid

### Datadog
- **Monitor ID not found**: Ensure payload includes `"id"` field
- **Event not posted**: Verify `DATADOG_API_KEY` and `DATADOG_SITE` are correct

### Grafana
- **Signature verification failed**: Check `GRAFANA_WEBHOOK_SECRET` matches webhook config
- **Alert UID not found**: Ensure alert has `__alert_uid__` label
- **Annotation not posted**: Verify `GRAFANA_API_KEY` and `GRAFANA_BASE_URL` are correct

### Slack
- **No notification appears**: Verify `SLACK_WEBHOOK_URL` is valid
- **Test endpoint returns skipped**: Set `SLACK_WEBHOOK_URL` in `.env`
- **Connection error**: Check webhook URL and network connectivity

---

## Log Analysis

All integration calls are logged comprehensively. Check logs for patterns:

```bash
# View all integration API calls
grep "API call\|API success\|API error" /path/to/runbookai.log

# View specific integration
grep "PagerDuty API" /path/to/runbookai.log
grep "Datadog API" /path/to/runbookai.log
grep "Grafana API" /path/to/runbookai.log
grep "Slack API" /path/to/runbookai.log
```

---

## Configuration Reference

| Variable | Purpose | Required? | Example |
|----------|---------|-----------|---------|
| `PAGERDUTY_WEBHOOK_SECRET` | Sign incoming webhooks | Optional | `wh_secret_123` |
| `PAGERDUTY_API_KEY` | Write back incident resolution | Optional | `u+4_Lk...` |
| `DATADOG_API_KEY` | Post resolution events | Optional | `dd_api_key_...` |
| `DATADOG_SITE` | Datadog instance | Optional | `datadoghq.com` |
| `GRAFANA_WEBHOOK_SECRET` | Sign incoming webhooks | Optional | `grafana_secret_123` |
| `GRAFANA_API_KEY` | Post alert closures | Optional | `eyJrOi...` |
| `GRAFANA_BASE_URL` | Grafana instance URL | Optional | `https://grafana.example.com` |
| `SLACK_WEBHOOK_URL` | Send notifications | Optional | `https://hooks.slack.com/...` |

---

## Next Steps

After testing:
1. Commit integration configuration to version control (excluding API keys)
2. Set up monitoring for integration health
3. Create runbooks for each integration
4. Test with real alerts from your monitoring stack
