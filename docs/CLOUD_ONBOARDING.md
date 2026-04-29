# RunbookAI Cloud Onboarding Guide

Get your VPC agent connected to RunbookAI cloud in **30 seconds**.

## Prerequisites

- Python 3.9+
- Access to a Linux server or VM in your VPC (or local testing)
- Internet connectivity to https://runbookai.cloud

## Step 1: Register with Cloud (1 minute)

Register your organization to get an API key:

```bash
curl -X POST https://runbookai.cloud/api/customers/register \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@yourcompany.com"}'
```

Response:
```json
{
  "customer_id": "cust_abc123def456",
  "api_key": "sk_xyz789uvw012...",
  "email": "ops@yourcompany.com",
  "created_at": "2026-04-28T12:00:00Z",
  "agent_setup_instructions": "..."
}
```

**Save the API key** — you'll use it to authenticate your agent.

## Step 2: Install Agent (5 seconds)

In the target VPC or local machine:

```bash
# Install the runbookai-agent package
pip install runbookai-agent
```

## Step 3: Configure Agent (10 seconds)

Store your API key:

```bash
runbookai config --api-key sk_xyz789uvw012...
```

This creates `~/.runbookai/config.json` with:
- Your API key
- Cloud API base URL (`https://runbookai.cloud`)
- MCP server port (default 7777)

## Step 4: Start Agent (1 second)

```bash
runbookai agent start
```

You should see:

```
Starting RunbookAI cloud agent...
  Cloud: https://runbookai.cloud
  MCP Port: 7777

Connected to RunbookAI cloud. Waiting for incidents...
```

**Done!** Your agent is now connected and ready to receive incidents.

## Step 5: Send Test Incident (optional)

Verify the agent is working:

```bash
curl -X POST https://runbookai.cloud/api/customers/cust_abc123def456/test-connection
```

Response:
```json
{
  "status": "ok",
  "message": "Test connection endpoint is ready..."
}
```

## Configuration

Agent configuration is stored in `~/.runbookai/config.json`:

```json
{
  "api_key": "sk_...",
  "cloud_url": "https://runbookai.cloud",
  "mcp_port": 7777
}
```

To view current configuration:

```bash
runbookai agent status
```

## Sending Incidents to Your Agent

Once your agent is registered and running, monitoring systems send incidents with your API key:

### Datadog Example

```bash
curl -X POST https://runbookai.cloud/webhooks/datadog \
  -H "X-API-Key: sk_xyz789uvw012..." \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "High CPU on prod-api-01",
    "service": "api"
  }'
```

The incident is automatically routed to your connected agent for processing.

### Generic Webhook Example

```bash
curl -X POST https://runbookai.cloud/webhooks/generic \
  -H "X-API-Key: sk_xyz789uvw012..." \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "Database connection pool exhausted",
    "service": "api",
    "severity": "critical"
  }'
```

## How It Works

1. **Agent Startup**: Agent connects to cloud and starts MCP server
2. **Heartbeat**: Agent sends heartbeat every 30 seconds to keep connection alive
3. **Incident Arrives**: Cloud receives webhook with your API key
4. **Routing**: Cloud routes incident to your online agent
5. **Execution**: Agent's MCP tools execute (SSH, metrics, logs) on your infrastructure
6. **Response**: Agent streams results back to cloud
7. **Resolution**: Cloud orchestrator decides next steps based on results

## Troubleshooting

### Agent won't connect

Check your API key:

```bash
echo $RUNBOOKAI_API_KEY  # Should show your key
runbookai agent status   # Should show connection status
```

Ensure internet connectivity to `https://runbookai.cloud`:

```bash
curl -I https://runbookai.cloud/health
# Should return 200 OK
```

### Agent logs

View agent logs:

```bash
runbookai agent logs
```

Or run with verbose logging:

```bash
RUNBOOKAI_LOG_LEVEL=debug runbookai agent start
```

### Connection lost

Agent automatically reconnects on connection loss. Incidents are queued while offline and delivered when agent comes back online.

To check agent status:

```bash
curl https://runbookai.cloud/api/agents/{customer_id}/status \
  -H "X-API-Key: sk_..."
```

## What Tools Does the Agent Have?

The agent can execute these tools on your infrastructure:

- `ssh_execute(host, command)` — Run commands on your servers
- `read_bmc_sensors(bmc_ip)` — Read hardware sensors (IPMI)
- `check_logs(host, service)` — Stream application logs
- `check_disk(host)` — Check disk usage
- `query_metrics(host)` — Performance metrics (CPU, memory, network)
- `check_processes(host)` — Monitor processes

All tools are executed within your VPC — cloud never has direct access to your infrastructure.

## Monitoring Your Agent

Check agent connection status from your dashboard:

```
https://runbookai.cloud/dashboard?api_key=sk_xyz789uvw012...
```

Shows:
- Agent online/offline status
- Last heartbeat timestamp
- Recent incidents
- Tool execution history

## Next Steps

1. **Configure Runbooks**: Define response procedures for your incidents
   - See `docs/RUNBOOK_GUIDE.md`

2. **Integrate Monitoring**: Connect Datadog, Grafana, or PagerDuty webhooks
   - See `docs/CLOUD_ARCHITECTURE.md` for webhook format

3. **Set Escalations**: Define email/Slack notifications if agent can't resolve
   - See `docs/ESCALATION_GUIDE.md`

## Support

For issues or questions:
- Email: support@runbookai.cloud
- Docs: https://runbookai.cloud/docs
- GitHub Issues: https://github.com/runbookai/runbookai/issues

## Advanced: Multi-Agent Setup

For redundancy, register multiple agents in the same customer account:

```bash
# Agent 1 (primary)
export RUNBOOKAI_API_KEY=sk_xyz...
runbookai agent start

# Agent 2 (replica, different machine)
export RUNBOOKAI_API_KEY=sk_xyz...  # Same API key!
runbookai agent start
```

Cloud automatically load-balances incidents across online agents.

## Advanced: Custom Configuration

Set custom cloud URL (for self-hosted deployments):

```bash
runbookai config \
  --api-key sk_xyz... \
  --cloud-url https://runbookai.mycompany.com \
  --mcp-port 8888
```

## Advanced: Local Testing

Test the cloud agent locally before VPC deployment:

```bash
# Start cloud + demo agent locally
cd demo/cloud
docker-compose up

# In another terminal, send test incident
curl -X POST http://localhost:7000/webhooks/generic \
  -H "X-API-Key: sk_demo_agent_token_12345" \
  -H "Content-Type: application/json" \
  -d '{"alert_name": "Test Alert", "service": "demo"}'
```

See `demo/cloud/docker-compose.yml` for full local setup.

## FAQ

**Q: Does the cloud have access to my infrastructure?**
A: No. Your agent runs in your VPC and controls all access. Cloud only sends incident descriptions and receives tool response results.

**Q: What happens if the agent goes offline?**
A: Incidents are queued in the database and delivered when the agent reconnects.

**Q: Can I run multiple agents in the same account?**
A: Yes. Use the same API key on multiple machines for redundancy.

**Q: How much does this cost?**
A: Pricing models are coming in Phase 3.4. Currently in beta, testing is free.

**Q: Can I self-host the cloud?**
A: Self-hosting coming in Phase 4. For now, only managed cloud (https://runbookai.cloud).
