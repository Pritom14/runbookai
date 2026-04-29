# Phase 2 Quick Start

## What's New

RunbookAI now integrates with **4 major monitoring platforms**:
1. PagerDuty — Receive incidents, resolve them, write status back
2. Datadog — Monitor webhooks, post resolution events
3. Grafana — Alert webhooks with HMAC verification, close alerts
4. Slack — Real-time incident notifications

## Deploy in 3 Steps

### Step 1: Copy the Latest Code
```bash
git pull origin main
# Phase 2 implementation is in 5 commits:
# - Step 1: PagerDuty enhancement
# - Step 2: Datadog integration
# - Step 3: Grafana integration
# - Step 4: Slack integration
# - Tests & Documentation
```

### Step 2: Configure Optional API Keys (in `.env`)
```bash
# Pick the platforms you use. Leave blank to skip.

# PagerDuty
PAGERDUTY_WEBHOOK_SECRET=your_secret
PAGERDUTY_API_KEY=your_api_key

# Datadog
DATADOG_API_KEY=your_api_key
DATADOG_SITE=datadoghq.com

# Grafana
GRAFANA_WEBHOOK_SECRET=your_secret
GRAFANA_API_KEY=your_api_key
GRAFANA_BASE_URL=https://grafana.example.com

# Slack (recommended!)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### Step 3: Restart RunbookAI
```bash
# Stop and restart your service
# New integrations are ready to use!
```

## Test It

### Quick Test
```bash
# Test Slack connectivity
curl -X POST http://localhost:7000/webhooks/slack/test

# Trigger a test alert
curl -X POST http://localhost:7000/webhooks/generic \
  -H "Content-Type: application/json" \
  -d '{"alert_name": "Test Alert", "service": "test-svc"}'

# Watch Slack for incident messages!
```

### Full Tests (if pytest installed)
```bash
pytest tests/test_integrations.py -v
```

## Set Up Webhooks in Your Platforms

### PagerDuty
1. Settings → Webhooks
2. Create webhook → `http://your-runbookai/webhooks/pagerduty`
3. Copy webhook secret → Set in `.env` as `PAGERDUTY_WEBHOOK_SECRET`
4. Events: Select "Incident Triggered"

### Datadog
1. Integrations → Webhooks
2. Create Custom Webhook
3. URL: `http://your-runbookai/webhooks/datadog`
4. Custom Payload (copy from Datadog docs)
5. Set webhook name in Datadog alerts

### Grafana
1. Alerts → Notification channels
2. New channel → Webhook
3. URL: `http://your-runbookai/webhooks/grafana`
4. HTTP POST method
5. Custom headers: (leave empty or add auth)
6. Set webhook secret → Set in `.env` as `GRAFANA_WEBHOOK_SECRET`

### Slack
1. Create Incoming Webhook at api.slack.com
2. Copy webhook URL
3. Set in `.env` as `SLACK_WEBHOOK_URL`
4. Done! Incidents automatically post to your channel

## What Happens Automatically

When an alert fires:

```
1. Webhook received → Parsed and validated
2. Incident created in RunbookAI
3. Slack notified: "[INCIDENT] Alert detected on Service"
4. Agent runs and attempts resolution
5. If resolved:
   - PagerDuty: Incident marked resolved
   - Datadog: Resolution event posted
   - Grafana: Closure annotation added
   - Slack: "[RESOLVED] Alert in 45s — Fixed disk space"
```

## Troubleshooting

### "API key not configured" warning
- This is normal! Only set API keys for platforms you use
- Integrations work without write-back (read-only mode)

### Slack message not appearing
1. Check `SLACK_WEBHOOK_URL` is set
2. Run: `curl -X POST http://localhost:7000/webhooks/slack/test`
3. Check logs: `grep "Slack API" runbookai.log`

### PagerDuty incident not marked resolved
1. Check `PAGERDUTY_API_KEY` is set
2. Check logs: `grep "PagerDuty API" runbookai.log`
3. Verify API key has "Write" permission in PagerDuty

### Datadog event not appearing
1. Check `DATADOG_API_KEY` and `DATADOG_SITE` are set
2. Check logs: `grep "Datadog API" runbookai.log`
3. Verify API key scope includes "Events Write"

### Grafana signature verification failed
1. Ensure `GRAFANA_WEBHOOK_SECRET` matches webhook config
2. Check logs for "signature verification failed"
3. Leave secret empty to skip verification (less secure)

## Key Features

✅ **Graceful Degradation** — Works without API keys (read-only)
✅ **Signature Verification** — HMAC-SHA256 for PagerDuty & Grafana
✅ **Comprehensive Logging** — All API calls logged for audit trail
✅ **Event Filtering** — Only actionable events trigger incidents
✅ **Write-back Capable** — Resolve incidents back in source platforms
✅ **Slack Integration** — Real-time formatted notifications
✅ **Production Ready** — Never crashes, handles all errors gracefully

## Documentation

- Full details: See `PHASE2_SUMMARY.md`
- Testing procedures: See `INTEGRATION_TESTING.md`
- Source code: `runbookai/integrations/*.py`

## Support

For issues or questions:
1. Check logs: Look for integration-specific messages
2. Run test endpoint: `curl -X POST http://localhost:7000/webhooks/slack/test`
3. Review: `INTEGRATION_TESTING.md` troubleshooting section
4. Check config: Verify `.env` has correct API keys

---

**You're ready to go!** Configure your platforms and watch RunbookAI handle incidents across your entire stack.
