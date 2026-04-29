# RunbookAI Cloud Architecture

## Overview

RunbookAI Phase 3 implements a managed cloud service (SaaS) architecture that allows customers to deploy lightweight agents in their own VPCs while RunbookAI cloud handles incident analysis, runbook matching, and orchestration.

## Architecture Components

### 1. Cloud Orchestrator (RunbookAI Hosted)

The central RunbookAI cloud service (`runbookai.cloud`) handles:

- **Customer Management**: Registration, API key generation, customer isolation
- **Incident Intake**: Receives webhooks from monitoring systems (Datadog, Grafana, PagerDuty)
- **Incident Routing**: Directs incidents to customers' VPC agents via SSE (Server-Sent Events)
- **Incident Queuing**: Queues incidents if agent is offline, delivers on reconnection
- **Runbook Matching**: Analyzes incidents and selects appropriate runbooks
- **Agent Orchestration**: Communicates with customer agents, streams tool calls, collects responses
- **Audit & Compliance**: Logs all incident activities for each customer

### 2. Customer VPC Agent (`runbookai-agent`)

A lightweight Python agent that runs inside the customer's VPC.

**Responsibilities:**
- Authenticates to cloud via API key (environment variable `RUNBOOKAI_API_KEY`)
- Maintains SSE connection to cloud for incident delivery
- Exposes MCP server (stdio transport) with customer-specific tools
- Executes tool calls from cloud orchestrator (SSH, BMC, metrics, etc.)
- Streams tool responses back to cloud
- Sends heartbeat every 30 seconds to keep connection alive

**Tools exposed:**
- `ssh_execute(host, command)` — SSH into customer's bastion/hosts
- `read_bmc_sensors(bmc_ip)` — IPMI sensor reads from BMC
- `check_logs(host, service)` — Stream logs from customer's servers
- `check_disk(host)` — Disk usage and filesystem checks
- `query_metrics(host)` — Performance metrics (CPU, memory, network)
- `check_processes(host)` — Process list and resource usage

### 3. Authentication & Multi-Tenancy

**API Key Format:** `sk_<random_token>` (e.g., `sk_abc123def456...`)

**Customer Registration Flow:**
1. Customer calls `/api/customers/register` with email
2. Cloud generates unique API key and stores Customer record
3. Cloud returns setup instructions for agent

**Incident Authentication:**
Every webhook must include the `X-API-Key` header to route to the correct customer:

```
POST /webhooks/generic
X-API-Key: sk_abc123def456...
Content-Type: application/json
{
  "alert_name": "High CPU on prod-api-01",
  "service": "api",
  "description": "CPU > 90% for 5 minutes"
}
```

### 4. Incident Routing

**Flow:**
1. Monitoring system sends webhook → `/webhooks/<source>` (PagerDuty, Datadog, etc.)
2. Webhook handler extracts `X-API-Key` → looks up customer
3. Customer's incident is created with `customer_id` and `api_key`
4. Routing layer checks if customer has online agent:
   - **If online**: Streams incident via SSE to agent
   - **If offline**: Queues incident in `pending_incidents` table
5. Agent receives incident via MCP server, processes it, streams responses back
6. Cloud orchestrator handles tool responses, decides next steps

**Offline Handling:**
- Incidents queue in `pending_incidents` table
- On agent reconnection, cloud delivers queued incidents
- Agent processes them in order they were received

### 5. Agent Lifecycle

**Agent Registration:**
- On startup, agent calls `/api/agents/{customer_id}/connect` with heartbeat
- Cloud creates or updates Agent record with status=online

**Heartbeat:**
- Every 30 seconds, agent sends heartbeat to `/api/agents/{customer_id}/heartbeat`
- Cloud updates `last_heartbeat` timestamp

**Connection Loss:**
- If no heartbeat for 2 minutes, cloud marks agent as offline
- Subsequent incidents are queued instead of streamed

**Graceful Shutdown:**
- Agent calls `/api/agents/{customer_id}/disconnect` before shutdown
- Cloud marks agent as offline

### 6. Database Schema

**Customer Table:**
```sql
CREATE TABLE customers (
  id VARCHAR PRIMARY KEY,           -- UUID
  api_key VARCHAR UNIQUE NOT NULL,  -- sk_<token>
  email VARCHAR NOT NULL,           -- Customer email
  created_at DATETIME,
  updated_at DATETIME
);
```

**Agent Table:**
```sql
CREATE TABLE agents (
  id VARCHAR PRIMARY KEY,                    -- UUID
  customer_id VARCHAR NOT NULL (FK),         -- links to customers.id
  name VARCHAR NOT NULL,                     -- e.g., "prod-agent-01"
  status VARCHAR NOT NULL,                   -- "online" | "offline" | "error"
  last_heartbeat DATETIME,                   -- timestamp of last heartbeat
  error_message TEXT,                        -- error details if status="error"
  created_at DATETIME,
  updated_at DATETIME
);
```

**Incident (modified):**
```sql
ALTER TABLE incidents ADD COLUMN customer_id VARCHAR (FK);  -- links to customers.id
```

**PendingIncident Table:**
```sql
CREATE TABLE pending_incidents (
  id VARCHAR PRIMARY KEY,            -- UUID
  customer_id VARCHAR NOT NULL (FK), -- links to customers.id
  incident_id VARCHAR NOT NULL (FK), -- links to incidents.id
  incident_payload JSON,             -- full incident details for re-delivery
  created_at DATETIME
);
```

## API Endpoints (Phase 3.1)

### Customer Management

**Register Customer:**
```
POST /api/customers/register
Content-Type: application/json

{
  "email": "ops@customer.com"
}

Response:
{
  "customer_id": "uuid...",
  "api_key": "sk_abc123...",
  "email": "ops@customer.com",
  "created_at": "2026-04-28T...",
  "agent_setup_instructions": "..."
}
```

**Get Customer Info:**
```
GET /api/customers/{customer_id}

Response:
{
  "customer_id": "uuid...",
  "email": "ops@customer.com",
  "created_at": "2026-04-28T..."
}
```

**Test Agent Connection:**
```
POST /api/customers/{customer_id}/test-connection

Response:
{
  "status": "ok",
  "message": "Test connection ready. Agent lifecycle management in Phase 3.2"
}
```

## Webhook Enhancement (Phase 3.1)

All webhook endpoints (`/webhooks/generic`, `/webhooks/pagerduty`, etc.) now accept an optional `X-API-Key` header:

```
POST /webhooks/generic
X-API-Key: sk_abc123...
Content-Type: application/json
{
  "alert_name": "High Memory",
  "service": "database"
}
```

- If `X-API-Key` is present: incident is routed to that customer
- If `X-API-Key` is missing: incident is processed as "cloud-less" (existing behavior)

## Example: End-to-End Flow

### Step 1: Customer Registration
```bash
curl -X POST https://runbookai.cloud/api/customers/register \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@acme.com"}'

# Response:
{
  "customer_id": "cust_12345",
  "api_key": "sk_abcd1234...",
  "agent_setup_instructions": "..."
}
```

### Step 2: Customer Sets Up Agent
```bash
# In customer's VPC
export RUNBOOKAI_API_KEY="sk_abcd1234..."
runbookai agent start
# Output: Connected to RunbookAI cloud. Waiting for incidents...
```

### Step 3: Incident Arrives
```bash
# From Datadog or other monitoring
curl -X POST https://runbookai.cloud/webhooks/datadog \
  -H "X-API-Key: sk_abcd1234..." \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "High CPU on prod-api-01",
    "service": "api"
  }'
```

### Step 4: Cloud Routes to Agent
1. Cloud looks up customer by API key
2. Finds customer's online agent
3. Streams incident via SSE to agent
4. Agent's MCP server receives incident
5. Agent executes tools (SSH, metrics, etc.) on customer's infrastructure
6. Agent streams responses back to cloud
7. Cloud orchestrator analyzes responses, decides next steps
8. Results logged to customer's incident dashboard

## Configuration

Cloud features are optional and gracefully degrade if not configured:

```python
# runbookai/config.py
class Settings:
    cloud_enabled: bool = False  # Enable cloud SaaS mode
    cloud_api_base_url: str = "https://runbookai.cloud"  # Agent connection URL
```

- If `cloud_enabled=False`: all cloud endpoints return 503 Service Unavailable
- Existing incident processing continues unchanged

## Security Considerations

1. **API Key Format**: Uses secure random tokens with `sk_` prefix
2. **API Key Storage**: Hashed in database (TODO: Phase 3.2)
3. **Customer Isolation**: Each customer can only access their own incidents
4. **Incident Payload Encryption**: Incidents queued in database contain full payload; should be encrypted (TODO: Phase 3.3)
5. **Tool Access Control**: Agent inherits customer's VPC network isolation; cannot access other customers' infrastructure
6. **Audit Logging**: All API calls logged with customer_id and timestamp

## Roadmap

- **Phase 3.1 (Current)**: Agent-based architecture, customer registration, API endpoints
- **Phase 3.2**: Agent lifecycle management (SSE, heartbeats, connection state)
- **Phase 3.3**: Incident dashboard, encrypted incident queuing
- **Phase 3.4**: Pricing models, metering, rate limiting
- **Phase 4**: Feature gating (Slack/PagerDuty only for paid tiers)

## Related Documentation

- `docs/CLOUD_ONBOARDING.md` — Quick start guide for customers
- `demo/cloud/docker-compose.yml` — Local cloud + agent demo setup
- `docs/PRICING_MODELS_TODO.md` — Pricing model research & implementation plan
